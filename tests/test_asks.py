"""claude-focus ask: a session queues a question instead of opening a dialog
that would block it; the question waits under YOUR TO-DOS, the session's
wait still runs, and Enter on the to-do tells the session to ask it now."""
import asyncio
import datetime as dt
import json
import time
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402
from claude_focus import asks, cli, pending, store  # noqa: E402
from claude_focus import queue as q  # noqa: E402

SESS = {"sessionId": "sid", "pid": 7, "name": "ultima-rebuild", "cwd": "/w/ultima2"}
CFG = {"aging_points_per_hour": 0, "aging_max": 0}


def row(status="idle", since=1000):
    return {"key": "sid", "sessionId": "sid", "pid": 7, "name": "ultima-rebuild", "cwd": "/w/ultima2",
            "status": status, "statusSince": since}


class AskTest(unittest.TestCase):
    def setUp(self):
        store.update_state(lambda st: (st.__setitem__("asks", {}), st.__setitem__("pending", {}),
                                       st.__setitem__("sessions", {})))

    def ask(self, *argv):
        with mock.patch.object(pending, "own_session", return_value=SESS), \
                mock.patch.object(pending, "app_running", return_value=True), \
                mock.patch("builtins.print") as out:
            cli.cmd_ask(list(argv))
        return " ".join(str(c.args[0]) for c in out.call_args_list)

    def test_one_question_from_flags(self):
        said = self.ask("--question", "Merge MR !412 now?", "--option", "Merge now::it passed review",
                        "--option", "Wait::after the demo", "--default", "the MR stays paused",
                        "--costly-after", "+2h")
        rec = asks.get("sid")
        self.assertEqual(rec["questions"][0]["options"][0], {"label": "Merge now", "description": "it passed review"})
        self.assertEqual(rec["default"], "the MR stays paused")
        self.assertGreater(rec["costly_after"], time.time() * 1000)
        self.assertIn("YOUR TO-DOS", said)
        self.assertEqual(json.loads(self.ask("--show"))["questions"][0]["question"], "Merge MR !412 now?")

    def test_up_to_four_from_json(self):
        qs = [{"question": f"Q{n}?", "options": [{"label": "a"}, {"label": "b"}]} for n in range(4)]
        self.ask("--json", json.dumps({"questions": qs}))
        self.assertEqual(len(asks.get("sid")["questions"]), 4)

    def test_what_is_refused(self):
        for argv in (["--question", "Only one option?", "--option", "a"],
                     ["--json", json.dumps({"questions": []})],
                     ["--option", "a", "--option", "b"]):
            with self.assertRaises(SystemExit):
                with mock.patch("sys.stderr"):
                    self.ask(*argv)
        self.assertIsNone(asks.get("sid"))

    def test_clear(self):
        self.ask("--question", "X?", "--option", "a", "--option", "b")
        self.assertIn("removed", self.ask("--clear"))
        self.assertIsNone(asks.get("sid"))


class QueueTest(unittest.TestCase):
    def classify(self, state, *rows):
        return q.classify(list(rows), state, dt.datetime.fromtimestamp(5000), CFG)

    def rec(self, sid="sid", at=1000, costly=None):
        return {"id": "q1", "pid": 7, "questions": [{"question": f"Q of {sid}?", "options": []}],
                "default": "paused", "costly_after": costly, "at": at}

    def test_a_queued_question_is_a_to_do_even_while_it_works_or_waits(self):
        for status in ("idle", "busy", "shell"):
            st = {"sessions": {}, "asks": {"sid": self.rec()}}
            v = self.classify(st, row(status))
            self.assertEqual([i["needs"] for i in v["todo"]], ["Q of sid?"], status)
        st = {"sessions": {}, "asks": {"sid": self.rec()},
              "pending": {"sid": {"pid": 7, "check": "true", "every_min": 5, "note": "MR !412 pipeline"}}}
        [item] = self.classify(st, row("idle"))["todo"]
        self.assertEqual(item["why"], "MR !412 pipeline")  # the wait runs on beside the question

    def test_an_open_dialog_stays_in_need_you(self):
        st = {"sessions": {}, "asks": {"sid": self.rec()}}
        self.assertEqual(self.classify(st, row("waiting"))["todo"], [])

    def test_a_question_of_another_process_is_not_shown(self):
        st = {"sessions": {}, "asks": {"sid": dict(self.rec(), pid=99)}}
        self.assertEqual(self.classify(st, row("busy"))["todo"], [])

    def test_costly_questions_come_first(self):
        other = dict(row("busy"), key="o", sessionId="o")
        st = {"sessions": {}, "asks": {"sid": self.rec(at=1000), "o": dict(self.rec("o", at=3000), costly_after=4000)}}
        v = self.classify(st, row("busy"), other)
        self.assertEqual([i["s"]["key"] for i in v["todo"]], ["o", "sid"])  # older, but not yet costly


class EnterTest(unittest.TestCase):
    def setUp(self):
        store.update_state(lambda st: st.__setitem__("asks", {}))
        self.app = appmod.App(None)
        self.app.bridge = mock.Mock(send_prompt=mock.AsyncMock(return_value=(True, "")))
        self.rec = asks.put(SESS, [{"question": "Merge now?", "options": [{"label": "a"}, {"label": "b"}]}])

    def test_enter_tells_the_session_to_ask_now(self):
        asyncio.run(self.app.deliver_ask(row(), "p1", self.rec))
        pane, text = self.app.bridge.send_prompt.await_args.args
        self.assertEqual(pane, "p1")
        self.assertTrue(text.startswith("[claude-focus ask]"))  # the prompt hook ignores it
        self.assertIn("Merge now?", text)
        self.assertIn("AskUserQuestion", text)
        self.assertIsNone(asks.get("sid"))  # passed on: it leaves the queue

    def test_a_draft_keeps_it(self):
        self.app.bridge.send_prompt = mock.AsyncMock(return_value=(False, "the prompt has a draft"))
        asyncio.run(self.app.deliver_ask(row(), "p1", self.rec))
        self.assertIsNotNone(asks.get("sid"))
        self.assertIn("draft", self.app.msg)

    def test_no_reminder_for_a_queued_question(self):
        self.app.cfg = dict(self.app.cfg, ask_nudge=True)
        self.app.bridge.pane_of = lambda s: "p1"
        self.app.bridge.user_pane = lambda: None
        with mock.patch.object(appmod.sessions_mod, "status_of", return_value=("idle", 1000)):
            asyncio.run(self.app._nudge(row(), {"kind": "decision", "level": 2, "level_p": 0.8}))
        self.app.bridge.send_prompt.assert_not_awaited()

    def test_typing_in_the_session_points_to_clear(self):
        text = pending.on_prompt({"session_id": "sid", "prompt": "merge it"})
        self.assertIn("claude-focus ask --clear", text)
        self.assertIsNone(pending.on_prompt({"session_id": "sid", "prompt": "[claude-focus wake-up] x"}))


class ParkedTest(unittest.TestCase):
    """A session moved to the background goes on under another id: its wait
    and question follow it (the 09:00 reminder of 2026-11-12 stayed behind)."""
    def test_the_wait_and_question_follow(self):
        store.update_state(lambda st: (st.__setitem__("pending", {"old": {"pid": 70177, "until": 9, "note": "TKDIS"}}),
                                       st.__setitem__("asks", {"old": {"id": "q1", "pid": 70177, "questions": []}})))
        app = appmod.App(None)
        app.sessions = [{"sessionId": "new", "pid": 89533, "name": "sellestial-cashflow-40", "parked_from": "old",
                         "jobId": "259bb86c"}]
        app._follow_parked()
        st = store.load_state()
        self.assertEqual((st["pending"]["new"]["pid"], st["pending"]["new"]["note"]), (89533, "TKDIS"))
        self.assertEqual(st["asks"]["new"]["pid"], 89533)
        self.assertNotIn("old", st["pending"])


if __name__ == "__main__":
    unittest.main()
