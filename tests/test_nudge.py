"""ask_nudge: a turn that leaves the user a decision in its text, not as a
question, gets a reminder to ask with AskUserQuestion; once in a while per
session, and never where it would get in the way."""
import asyncio
import time
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402
from claude_focus import store  # noqa: E402

DECISION = {"kind": "decision", "level": 2, "level_p": 0.7, "ask": "Povej, ali naj ..."}


class NudgeTest(unittest.TestCase):
    def setUp(self):
        store.update_state(lambda st: (st.__setitem__("sessions", {}), st.__setitem__("pending", {})))
        self.app = appmod.App(None)
        self.app.cfg = dict(self.app.cfg, ask_nudge=True)
        self.app.bridge = mock.Mock(send_prompt=mock.AsyncMock(return_value=(True, "")),
                                    pane_of=lambda s: "p1", user_pane=lambda: None)
        self.s = {"key": "k", "sessionId": "sid", "pid": 7, "name": "dobri-filmi", "status": "idle",
                  "statusSince": 100}

    def nudge(self, sc=DECISION, status=("idle", 100)):
        with mock.patch.object(appmod.sessions_mod, "status_of", return_value=status):
            asyncio.run(self.app._nudge(self.s, sc))
        return self.app.bridge.send_prompt.await_args_list

    def test_a_decision_in_the_text(self):
        [call] = self.nudge()
        self.assertEqual(call.args[0], "p1")
        self.assertTrue(call.args[1].startswith("[claude-focus reminder]"))  # the prompt hook ignores it
        self.assertIn("AskUserQuestion", call.args[1])

    def test_once_in_a_while(self):
        self.nudge()
        self.s["statusSince"] = 200  # it answered in text again
        self.assertEqual(len(self.nudge(status=("idle", 200))), 1)
        store.update_state(lambda st: st["sessions"]["k"].__setitem__("nudged_at", time.time() - 3600))
        self.assertEqual(len(self.nudge(status=("idle", 200))), 2)

    def test_not_when(self):
        cases = [
            ("off", dict(cfg=False), DECISION, ("idle", 100)),
            ("nothing needed", {}, {"kind": "info", "level": 0, "level_p": 0.9}, ("idle", 100)),
            ("it moved on", {}, DECISION, ("busy", 150)),
        ]
        for name, opt, sc, status in cases:
            self.app.cfg["ask_nudge"] = opt.get("cfg", True)
            self.assertEqual(self.nudge(sc, status), [], name)
        self.app.cfg["ask_nudge"] = True
        self.s["status"] = "waiting"  # a question is open already
        self.assertEqual(self.nudge(), [])
        self.s["status"] = "idle"
        store.update_state(lambda st: st["pending"].__setitem__("sid", {"pid": 7, "note": "waits"}))
        self.assertEqual(self.nudge(), [], "it waits for something")
        store.update_state(lambda st: st["pending"].clear())
        self.app.bridge.user_pane = lambda: "p1"  # the user is in it
        with mock.patch.object(self.app, "_user_in", return_value=True):
            self.assertEqual(self.nudge(), [])


if __name__ == "__main__":
    unittest.main()
