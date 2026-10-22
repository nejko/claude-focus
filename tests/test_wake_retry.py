import asyncio
import os
import tempfile
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402
from claude_focus import pending as pend  # noqa: E402
from claude_focus import store  # noqa: E402
from claude_focus.screen import DRAFT, EMPTY  # noqa: E402

STAMP = 1790915096936


class FakeBridge:
    def __init__(self, screen):
        self.screen, self.sent = screen, []

    async def prompt_state(self, pane):
        return self.screen

    async def send_prompt(self, pane, text):
        self.sent.append(text)
        return True, ""


class RetryTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        names = {"DIR": d, "STATE": os.path.join(d, "state.json"), "LOCK": os.path.join(d, ".lock"),
                 "LOG": os.path.join(d, "focus.log"), "PRIORITIES": os.path.join(d, "p.md")}
        for name, value in names.items():
            p = mock.patch.object(store, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.app = appmod.App(None)
        self.app.sessions = [{"sessionId": "a", "key": "a", "name": "release-notes", "pid": 1,
                              "status": "idle", "statusSince": STAMP}]
        self.app.pane_of = lambda s: "pane"
        self.app._user_in = lambda s: False

    def retry(self, entry, screen=EMPTY):
        store.update_state(lambda st: st["sessions"].__setitem__("a", entry))
        self.app.bridge = FakeBridge(screen)
        asyncio.run(self.app.retry_wakeups(store.load_state()))
        return self.app.bridge.sent

    def woke(self, **kw):
        return dict({"at": STAMP + 60000, "reason": "the check succeeded", "delivered": False,
                     "stamp": STAMP}, **kw)

    def test_types_the_late_wakeup_once_the_prompt_is_empty(self):
        self.assertEqual(self.retry({"woke": self.woke()}, screen=DRAFT), [])
        sent = self.retry({"woke": self.woke()})
        self.assertTrue(sent[0].startswith(pend.WAKE_PREFIX))
        self.assertTrue(store.load_state()["sessions"]["a"]["woke"]["delivered"])
        self.assertEqual(self.retry(store.load_state()["sessions"]["a"]), [])  # never twice

    def test_a_session_whose_shells_run_takes_the_wakeup(self):
        # Its turn has ended; only background shells it started still run.
        self.app.sessions[0]["status"] = "shell"
        self.assertTrue(self.retry({"woke": self.woke()})[0].startswith(pend.WAKE_PREFIX))
        self.app.bridge = FakeBridge(EMPTY)
        asyncio.run(self.app.fire(self.app.sessions[0], {"until": STAMP, "note": "slides"}, "time"))
        self.assertTrue(self.app.bridge.sent[0].startswith(pend.WAKE_PREFIX))
        self.assertTrue(store.load_state()["sessions"]["a"]["woke"]["delivered"])

    def test_types_it_while_snoozed(self):
        self.assertEqual(len(self.retry({"woke": self.woke(), "snooze_until": STAMP * 2,
                                         "snooze_stamp": STAMP})), 1)

    def test_not_after_done_a_state_change_or_with_the_user_in_the_pane(self):
        self.assertEqual(self.retry({"woke": self.woke(), "dismissed_stamp": STAMP}), [])
        self.assertEqual(self.retry({"woke": self.woke(stamp=STAMP - 1)}), [])
        self.app._user_in = lambda s: True
        self.assertEqual(self.retry({"woke": self.woke()}), [])

    def test_saved_message_is_reused(self):
        sent = self.retry({"woke": self.woke(message=pend.WAKE_PREFIX + " Your check succeeded.")})
        self.assertIn("Your check succeeded.", sent[0])
        self.assertIn("This wake-up is late", sent[0])


    def test_done_key_is_refused_for_an_open_question_and_logged_otherwise(self):
        s = self.app.sessions[0]
        for status, done in (("waiting", False), ("idle", True)):
            s["status"] = status
            self.app.view = {"need": [{"s": s}], "working": [], "pending": [], "free": []}
            self.app.dismiss()
            e = store.load_state()["sessions"].get("a", {})
            self.assertEqual("dismissed_stamp" in e, done, status)
        with open(store.LOG) as f:
            self.assertIn("key: release-notes marked done", f.read())


    def test_done_on_a_wait_with_a_request_marks_only_the_users_part(self):
        s = self.app.sessions[0]
        pend.set_pending(s, STAMP * 2, "true", 10, "the export", "Send the draft")
        self.app.view = {"need": [], "todo": [{"s": s, "needs": "Send the draft", "why": "the export"}],
                         "working": [], "pending": [], "free": []}
        self.app.dismiss()
        p = store.load_state()["pending"]["a"]
        self.assertTrue(p["needs_done"])
        self.assertEqual(p["note"], "the export")  # the wait itself stays


if __name__ == "__main__":
    unittest.main()
