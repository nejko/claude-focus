"""The herdr bridge against a fake herdr server on a scratch socket. The
replies have the shapes that herdr 0.9.5 (API protocol 22) gave on
2026-10-21."""
import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402
from claude_focus import herdr, store  # noqa: E402
from claude_focus.screen import DRAFT, EMPTY  # noqa: E402

RULE = "\x1b[0m\x1b[38;2;136;136;136m" + "─" * 60 + "\x1b[0m"
SUGGESTION = f"done.\n{RULE}\n❯\xa0\x1b[0m\x1b[2mTry \"fix typecheck errors\"\x1b[0m\n{RULE}\n  ⏵⏵ auto mode on"
TYPED = f"done.\n{RULE}\n❯\xa0hello draft\n{RULE}\n  ⏵⏵ auto mode on"


def pane(pane_id, agent=None, session=None, focused=False, cwd="/Users/ana/Projects/api"):
    ws, _ = pane_id.split(":")
    row = {"pane_id": pane_id, "workspace_id": ws, "tab_id": f"{ws}:t1", "focused": focused, "cwd": cwd,
           "foreground_cwd": cwd, "agent": agent, "agent_status": "idle" if agent else "unknown",
           "terminal_id": "term_" + pane_id}
    if session:
        row["agent_session"] = {"source": "herdr:claude", "agent": "claude", "kind": "id", "value": session}
    return row


class FakeHerdr:
    """Answers each request on its own connection, as herdr does, and keeps
    the calls. procs: pane id -> (shell pid, [(pid, name), ...] in front)."""

    def __init__(self):
        self.path = os.path.join(tempfile.mkdtemp(), "h.sock")
        self.calls = []
        self.panes, self.procs, self.screens = [], {}, {}

    def answer(self, method, p):
        if method == "ping":
            return {"type": "pong", "version": "0.9.5", "protocol": 22}
        if method == "pane.list":
            return {"type": "pane_list", "panes": [dict(r) for r in self.panes]}
        if method == "pane.process_info":
            shell, procs = self.procs.get(p["pane_id"], (None, []))
            return {"type": "pane_process_info", "process_info": {
                "pane_id": p["pane_id"], "shell_pid": shell, "foreground_process_group_id": shell,
                "foreground_processes": [{"pid": pid, "name": name} for pid, name in procs]}}
        if method == "pane.get":
            return {"type": "pane_info", "pane": next(r for r in self.panes if r["pane_id"] == p["pane_id"])}
        if method == "pane.read":
            text = self.screens.get(p["pane_id"], "")
            return {"type": "pane_read", "read": {"pane_id": p["pane_id"], "source": p["source"],
                                                  "text": text if p.get("strip_ansi") is False else
                                                  herdr.strip_ansi(text)}}
        if method == "workspace.list":
            return {"workspaces": [{"workspace_id": "w1", "label": "api"}, {"workspace_id": "w2", "label": "web"}]}
        if method == "tab.list":
            return {"tabs": [{"tab_id": "w1:t1", "label": "1"}, {"tab_id": "w2:t1", "label": "2"}]}
        if method == "tab.create":
            self.panes.append(pane("w1:p9"))
            self.procs["w1:p9"] = (900, [(900, "zsh")])
            return {"type": "tab_created", "tab": {"tab_id": "w1:t2"}, "root_pane": {"pane_id": "w1:p9"}}
        return {"type": "ok"}

    async def handle(self, reader, writer):
        req = json.loads(await reader.readline())
        self.calls.append((req["method"], req.get("params") or {}))
        writer.write((json.dumps({"id": req["id"], "result": self.answer(req["method"], req["params"])}) + "\n")
                     .encode())
        await writer.drain()
        writer.close()

    def sent(self, *methods):
        return [(m, p) for m, p in self.calls if m in methods]


class BridgeTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        p = mock.patch.object(store, "LOG", os.path.join(d, "focus.log"))
        p.start()
        self.addCleanup(p.stop)
        self.front = True
        p = mock.patch.object(herdr, "front_is_herdr", side_effect=lambda: self.front)
        p.start()
        self.addCleanup(p.stop)
        self.fake = FakeHerdr()
        self.fake.panes = [pane("w1:p1", "claude", "s-1", focused=True), pane("w1:p2"),
                           pane("w2:p1", "claude", "s-2"), pane("w2:p2", cwd="/Users/ana")]
        self.fake.procs = {"w1:p1": (100, [(101, "2.1.306")]), "w1:p2": (200, [(200, "zsh")]),
                           "w2:p1": (300, [(300, "zsh")]),  # its Claude Code is suspended (Ctrl-Z)
                           "w2:p2": (400, [(401, "vim")])}

    def run_bridge(self, fn):
        async def main():
            server = await asyncio.start_unix_server(self.fake.handle, path=self.fake.path)
            try:
                bridge = herdr.Bridge(self.fake.path)
                await bridge.refresh()
                return await fn(bridge)
            finally:
                server.close()
                await server.wait_closed()
        return asyncio.run(main())

    def test_a_session_is_found_by_its_process_else_by_its_session_id(self):
        async def find(b):
            return [b.pane_of({"pid": 101, "sessionId": "s-1"}),
                    b.pane_of({"pid": 202, "sessionId": "s-2"}),   # not in front: the hook's session id
                    b.pane_of({"pid": 999, "sessionId": "s-9"})]
        self.assertEqual(self.run_bridge(find), ["w1:p1", "w2:p1", None])

    def test_two_panes_of_one_conversation_are_told_apart_by_process(self):
        self.fake.panes.append(pane("w1:p3", "claude", "s-1"))
        self.fake.procs["w1:p3"] = (500, [(501, "2.1.306")])

        async def find(b):
            return [b.pane_of({"pid": 101, "sessionId": "s-1"}), b.pane_of({"pid": 501, "sessionId": "s-1"}),
                    b.pane_of({"pid": 777, "sessionId": "s-1"})]  # which one? none rather than the wrong one
        self.assertEqual(self.run_bridge(find), ["w1:p1", "w1:p3", None])

    def test_the_user_is_in_the_focused_pane_only_while_herdr_is_in_front(self):
        self.assertEqual(self.run_bridge(lambda b: asyncio.sleep(0, b.user_pane())), "w1:p1")
        self.front = False
        self.assertIsNone(self.run_bridge(lambda b: asyncio.sleep(0, b.user_pane())))
        self.front = None  # macOS does not say: trust herdr's focus
        self.assertEqual(self.run_bridge(lambda b: asyncio.sleep(0, b.user_pane())), "w1:p1")

    def test_free_terminals_are_panes_at_a_bare_shell(self):
        app = appmod.App(None)
        sessions = [{"pid": 101, "sessionId": "s-1", "key": "s-1"}]
        self.fake.panes.append(pane("w2:p3"))  # the focus screen's own pane
        self.fake.procs["w2:p3"] = (600, [(600, "zsh")])

        async def free(b):
            app.bridge, app.sessions = b, sessions
            with mock.patch.dict(os.environ, {"HERDR_PANE_ID": "w2:p3"}):
                return await app._free_terminals()
        terms = self.run_bridge(free)
        self.assertEqual([(t["pane"], t["tty"], t["job"]) for t in terms], [("w1:p2", "api:1.2", "zsh")])
        self.assertEqual(terms[0]["path"], "/Users/ana/Projects/api")

    def test_pane_info_names_the_agent_else_the_program(self):
        info = self.run_bridge(lambda b: b.pane_info())
        self.assertEqual([(p, where, job) for p, where, _, _, job in info],
                         [("w1:p1", "api:1.1", "claude"), ("w1:p2", "api:1.2", "zsh"),
                          ("w2:p1", "web:2.1", "claude"), ("w2:p2", "web:2.2", "vim")])

    def test_wakeup_is_typed_into_an_empty_prompt_only(self):
        async def send(b):
            with mock.patch.object(herdr.asyncio, "sleep", mock.AsyncMock()):
                return await b.send_prompt("w1:p1", "[claude-focus wake-up]\nThe deploy\nis done.")
        self.fake.screens["w1:p1"] = SUGGESTION
        self.assertEqual(self.run_bridge(send), (True, ""))
        self.assertEqual(self.fake.sent("pane.send_text", "pane.send_keys"), [
            ("pane.send_text", {"pane_id": "w1:p1", "text": "[claude-focus wake-up] The deploy is done."}),
            ("pane.send_keys", {"pane_id": "w1:p1", "keys": ["enter"]})])
        self.fake.calls, self.fake.screens["w1:p1"] = [], TYPED
        self.assertEqual(self.run_bridge(send), (False, "the prompt has a draft"))
        self.assertEqual(self.fake.sent("pane.send_text", "pane.send_keys"), [])

    def test_prompt_state(self):
        self.fake.screens["w1:p1"] = SUGGESTION
        self.assertEqual(self.run_bridge(lambda b: b.prompt_state("w1:p1")), EMPTY)
        self.fake.screens["w1:p1"] = TYPED
        self.assertEqual(self.run_bridge(lambda b: b.prompt_state("w1:p1")), DRAFT)
        self.assertIn("hello draft", self.run_bridge(lambda b: b.screen_text("w1:p1")))

    def test_go_focuses_and_zooms(self):
        self.assertTrue(self.run_bridge(lambda b: b.go("w2:p1")))
        self.assertEqual(self.fake.sent("pane.focus", "pane.zoom"), [
            ("pane.focus", {"pane_id": "w2:p1"}), ("pane.zoom", {"pane_id": "w2:p1", "mode": "on"})])
        self.fake.calls = []
        self.run_bridge(lambda b: b.go("w2:p1", zoom=False))
        self.assertEqual(self.fake.sent("pane.focus", "pane.zoom"), [("pane.focus", {"pane_id": "w2:p1"})])

    def test_a_new_tab_gets_the_command_once_its_shell_runs(self):
        self.assertTrue(self.run_bridge(lambda b: b.open_window("claude attach 7")))
        self.assertEqual(self.fake.sent("tab.create")[0][1]["focus"], True)
        self.assertEqual(self.fake.sent("pane.send_text"),
                         [("pane.send_text", {"pane_id": "w1:p9", "text": "claude attach 7\r"})])

    def test_sidebar_tokens_are_sent_when_they_change_and_cleared_after(self):
        async def report(b):
            await b.report({"w1:p1": ("need you", 87.4, "Approve the deploy")})
            await b.report({"w1:p1": ("need you", 87.4, "Approve the deploy")})  # unchanged: not sent
            await b.report({})
        self.run_bridge(report)
        sent = [p for _, p in self.fake.sent("pane.report_metadata")]
        self.assertEqual([p["tokens"] for p in sent], [
            {"cf_state": "need you", "cf_priority": "87", "cf_ask": "Approve the deploy"},
            {"cf_state": None, "cf_priority": None, "cf_ask": None}])
        self.assertEqual(sent[0]["ttl_ms"], herdr.TOKEN_TTL_MS)
        self.assertEqual({p["source"] for p in sent}, {"claude-focus"})

    def test_a_long_request_is_cut_for_the_sidebar(self):
        tokens = herdr.sidebar_tokens("to-do", None, "word " * 40)
        self.assertEqual((len(tokens["cf_ask"]), tokens["cf_ask"][-1], tokens["cf_priority"]),
                         (herdr.ASK_CELLS, "…", ""))

    def test_the_bridge_gives_up_when_herdr_is_gone(self):
        b = herdr.Bridge(os.path.join(tempfile.mkdtemp(), "none.sock"))

        async def refresh():
            for _ in range(herdr.GONE_AFTER - 1):
                with self.assertRaises(herdr.Error):
                    await b.refresh()
            await b.refresh()
        with self.assertRaises(SystemExit):
            asyncio.run(refresh())

    def test_ping(self):
        async def ping(b):
            return await asyncio.get_event_loop().run_in_executor(None, herdr.ping, self.fake.path)
        self.assertEqual(self.run_bridge(ping)["protocol"], herdr.PROTOCOL)
        self.assertIsNone(herdr.ping(os.path.join(tempfile.mkdtemp(), "none.sock")))


class MarksTest(unittest.TestCase):
    def test_each_session_with_a_pane_gets_its_place_in_the_queue(self):
        app = appmod.App(None)
        app.pane_of = lambda s: {"a": "w1:p1", "b": "w1:p2", "c": "w1:p3"}.get(s["sessionId"])
        s = lambda sid: {"sessionId": sid, "key": sid}  # noqa: E731
        app.view = {"need": [{"s": s("a"), "fresh": {"ask": "Approve it"}, "eff": 81.0}],
                    "todo": [{"s": s("b"), "needs": "Send the draft"}],
                    "pending": [], "working": [{"s": s("c")}], "free": [{"s": s("x"), "fresh": None, "why": ""}],
                    "terminals": [{"s": {"key": "pane:w2:p1"}, "pane": "w2:p1"}]}
        self.assertEqual(app._marks(), {"w1:p1": ("need you", 81.0, "Approve it"),
                                        "w1:p2": ("to-do", None, "Send the draft"),
                                        "w1:p3": ("working", None, "")})


class FrontAppTest(unittest.TestCase):
    PS = """\
  10     1 /Applications/Ghostty.app/Contents/MacOS/ghostty
  11    10 /usr/bin/login -fpl ana /bin/zsh
  12    11 -zsh
  13    12 herdr
  20     1 /Users/ana/.nix-profile/bin/herdr server
  30     1 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
"""

    def test_herdr_is_in_front_when_the_front_app_runs_a_herdr_client(self):
        self.assertTrue(herdr.front_is_herdr(self.PS, front_pid=10))
        self.assertFalse(herdr.front_is_herdr(self.PS, front_pid=30))
        self.assertFalse(herdr.front_is_herdr(self.PS.replace(" herdr\n", " vim\n"), front_pid=10))


if __name__ == "__main__":
    unittest.main()
