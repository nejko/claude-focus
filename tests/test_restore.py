import json
import os
import tempfile
import time
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import restore, store  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def claude(sid, cwd=HERE, **kw):
    return dict({"kind": "claude", "sessionId": sid, "name": sid, "cwd": cwd,
                 "flags": ["--dangerously-skip-permissions"], "pending": None}, **kw)


def snap(at, run, *sids, full=True):
    return {"at": at, "run": run,
            "windows": [{"fullscreen": full, "frame": [-1920, 37, 1920, 1080], "panes": [claude(s)]} for s in sids]}


class FlagsTest(unittest.TestCase):
    def test_keeps_permission_and_model_flags_only(self):
        self.assertEqual(restore.keep_flags("claude --dangerously-skip-permissions -r"),
                         ["--dangerously-skip-permissions"])
        self.assertEqual(restore.keep_flags("claude --model opus --resume abc -n x --permission-mode plan"),
                         ["--model", "opus", "--permission-mode", "plan"])
        self.assertEqual(restore.keep_flags("claude --model=opus -c"), ["--model=opus"])
        self.assertEqual(restore.keep_flags("claude"), [])

    def test_shell_line(self):
        p = claude("abc", cwd="/Users/n/My Projects/x")
        self.assertEqual(restore.shell_line(p),
                         "cd '/Users/n/My Projects/x' && claude --dangerously-skip-permissions --resume abc")
        self.assertEqual(restore.shell_line({"kind": "focus", "cwd": "/"}), "claude-focus")


class HistoryTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        names = {"DIR": d, "PRIORITIES": os.path.join(d, "p.md"), "LOG": os.path.join(d, "focus.log"),
                 "STATE": os.path.join(d, "state.json"), "LOCK": os.path.join(d, ".lock")}
        for name, value in names.items():
            p = mock.patch.object(store, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(restore, "HISTORY", os.path.join(d, "layouts.jsonl"))
        p.start()
        self.addCleanup(p.stop)

    def test_record_appends_only_changes_and_reader_skips_a_cut_line(self):
        s = {"windows": [{"fullscreen": True, "frame": [0, 0, 1, 1], "panes": [claude("a")]}]}
        sig = restore.record(s, 100, None)
        self.assertEqual(restore.record(s, 100, sig), sig)
        restore.record({"windows": []}, 100, sig)
        with open(restore.HISTORY, "a") as f:
            f.write('{"windows": [{"fullscr')  # the crash cut this line
        lines = restore.read_history()
        self.assertEqual([len(d["windows"]) for d in lines], [1, 0])
        self.assertEqual(lines[0]["run"], 100)

    def test_prune_keeps_the_previous_run_however_old(self):
        old = (time.time() - 30 * 86400) * 1000
        new = time.time() * 1000
        with open(restore.HISTORY, "w") as f:
            for d in (snap(old, 1, "a"), snap(old + 1, 2, "b"), snap(new, 3, "c")):
                f.write(json.dumps(d) + "\n")
        with mock.patch.object(restore, "PRUNE_BYTES", 0):
            restore.prune(3)
        self.assertEqual([d["run"] for d in restore.read_history()], [2, 3])


class ChooseTest(unittest.TestCase):
    def test_last_snapshot_of_the_previous_run(self):
        lines = [snap(1, 10, "a"), snap(2, 10, "a", "b"), snap(500_000, 10, "a", "b", "c"),
                 snap(600_000, 20, "x")]
        self.assertEqual(restore.claude_count(restore.previous_layout(lines, 20)), 3)
        self.assertIsNone(restore.previous_layout([snap(1, 20, "x")], 20))

    def test_panes_closing_while_iterm_quits_do_not_count(self):
        lines = [snap(0, 10, "a", "b", "c"), snap(30_000, 10, "a", "b"), snap(40_000, 10, "a"),
                 snap(41_000, 10)]
        self.assertEqual(restore.claude_count(restore.previous_layout(lines, 20)), 3)

    def test_a_close_long_before_the_end_counts(self):
        lines = [snap(0, 10, "a", "b"), snap(600_000, 10, "a")]
        self.assertEqual(restore.claude_count(restore.previous_layout(lines, 20)), 1)


class RestartTest(unittest.TestCase):
    def test_a_save_for_a_restart_wins_when_the_run_ends_soon_after(self):
        saved = dict(snap(1_000_000, 10, "a", "b", "c"), saved="restart", auto_restore=True)
        closing = [snap(1_000_000 + n * 60_000, 10, *"abc"[:3 - n]) for n in range(1, 4)]
        self.assertIs(restore.previous_layout([saved] + closing, 20), saved)
        later = snap(1_000_000 + restore.SAVE_VALID_MS + 1, 10, "a")
        self.assertIsNot(restore.previous_layout([saved] + closing + [later], 20), saved)

    def test_first_message_by_status(self):
        busy = claude("abc", status="busy")
        line = restore.shell_line(busy)
        self.assertIn("--resume abc '[claude-focus restore] The user restarted the computer", line)
        self.assertIn("continue the task", line)
        self.assertIn("ask the same question again", restore.shell_line(claude("abc", status="waiting")))
        self.assertTrue(restore.shell_line(claude("abc", status="idle")).endswith("--resume abc"))

    def test_the_prompt_hook_ignores_the_restore_message(self):
        from claude_focus import pending as pend
        self.assertIsNone(pend.on_prompt({"session_id": "x", "prompt": restore.RESUME_MESSAGES["busy"]}))

    def test_auto_restore_runs_once_and_only_for_a_save(self):
        import asyncio
        d = tempfile.mkdtemp()
        names = {"DIR": d, "STATE": os.path.join(d, "state.json"), "LOCK": os.path.join(d, ".lock"),
                 "LOG": os.path.join(d, "focus.log"), "PRIORITIES": os.path.join(d, "p.md")}
        patches = [mock.patch.object(store, k, v) for k, v in names.items()]
        patches += [mock.patch.object(restore, "HISTORY", os.path.join(d, "layouts.jsonl")),
                    mock.patch.object(restore.sessions_mod, "list_sessions", return_value=[]),
                    mock.patch.object(restore, "focus_pane", return_value=None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        opened = []

        async def fake_run(conn, windows, say):
            opened.append(len(windows))
        with mock.patch.object(restore, "run", fake_run):
            with open(restore.HISTORY, "w") as f:
                f.write(json.dumps(snap(time.time() * 1000, 10, "a", "b")) + "\n")
            self.assertEqual(asyncio.run(restore.auto(None, 20)), 0)  # a crash: R, not by itself
            restore.save({"windows": snap(0, 0, "a", "b")["windows"]}, 10)
            self.assertEqual(asyncio.run(restore.auto(None, 20)), 2)
            self.assertEqual(asyncio.run(restore.auto(None, 20)), 0)  # never twice
        self.assertEqual(opened, [2])


class PlanTest(unittest.TestCase):
    def test_skips_running_gone_duplicate_and_open_focus(self):
        s = {"at": 1, "run": 1, "windows": [
            {"fullscreen": True, "frame": None, "panes": [{"kind": "focus", "cwd": "/"}]},
            {"fullscreen": True, "frame": None, "panes": [claude("runs")]},
            {"fullscreen": True, "frame": None, "panes": [claude("gone", cwd="/no/such/folder")]},
            {"fullscreen": True, "frame": None, "panes": [claude("a")]},
            {"fullscreen": False, "frame": None, "panes": [claude("a")]},
        ]}
        windows, skipped = restore.plan(s, {"runs"}, focus_running=True)
        self.assertEqual([[p.get("sessionId") for p in w["panes"]] for w in windows], [["a"]])
        self.assertEqual([(p["sessionId"], why) for p, why in skipped], [("gone", "its folder is gone")])
        windows, _ = restore.plan(s, {"runs"}, focus_running=False)
        self.assertEqual(windows[0]["panes"][0]["kind"], "focus")


class TurnTest(unittest.TestCase):
    def run_lines(self, entries):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(e) for e in entries))
        with mock.patch.object(restore.sessions_mod, "transcript_path", return_value=f.name):
            return restore.turn_was_open("/x", "id")

    def user(self, content):
        return {"type": "user", "message": {"content": content}}

    def assistant(self, *blocks):
        return {"type": "assistant", "message": {"content": list(blocks)}}

    def test_turn_states(self):
        text = {"type": "text", "text": "Done."}
        tool = {"type": "tool_use", "id": "t", "name": "Bash", "input": {}}
        result = [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]
        self.assertFalse(self.run_lines([self.user("hi"), self.assistant(text)]))
        self.assertTrue(self.run_lines([self.user("hi"), self.assistant(tool)]))
        self.assertTrue(self.run_lines([self.user("hi"), self.assistant(tool), self.user(result)]))
        self.assertTrue(self.run_lines([self.assistant(text), self.user("next task")]))
        self.assertFalse(self.run_lines([self.assistant(tool), self.user("[Request interrupted by user]")]))
        self.assertFalse(self.run_lines([self.assistant(text),
                                         self.user("<command-name>/exit</command-name>")]))


if __name__ == "__main__":
    unittest.main()
