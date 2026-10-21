import asyncio
import os
import tempfile
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402
from claude_focus import store  # noqa: E402

OPEN = ("waiting", 100)


class FakeBridge:
    def __init__(self, focus):
        self.focus, self.back = focus, 0

    async def has_focus(self, pane):
        return self.focus

    async def activate_own(self):
        self.back += 1


class ReturnAfterAnswerTest(unittest.TestCase):
    """The status file and transcript values are the ones a real session
    wrote in a test on 2026-10-13 (two questions answered, Esc, Chat about this)."""

    def setUp(self):
        p = mock.patch.object(store, "LOG", os.path.join(tempfile.mkdtemp(), "focus.log"))
        p.start()
        self.addCleanup(p.stop)

    def watch(self, statuses, question, focus=True, before="t0"):
        app = appmod.App(None)
        app.bridge = FakeBridge(focus)
        statuses = iter(statuses)

        async def no_wait(_):
            pass
        with mock.patch.object(appmod.sessions_mod, "status_of", lambda pid, sid: next(statuses)), \
                mock.patch.object(appmod.sessions_mod, "last_question", lambda cwd, sid: question), \
                mock.patch.object(appmod.asyncio, "sleep", no_wait):
            asyncio.run(app._return_after_answer({"pid": 1, "sessionId": "a", "cwd": "/x", "name": "a"},
                                                 "pane", OPEN, before))
        return app.bridge.back

    def test_answered_question_brings_the_focus_window_back(self):
        self.assertEqual(self.watch([OPEN, OPEN, ("busy", 200)], ("t1", "answered")), 1)

    def test_answer_followed_by_a_new_question(self):
        self.assertEqual(self.watch([OPEN, ("waiting", 300)], ("t1", "answered")), 1)

    def test_esc_or_chat_about_this_keeps_the_focus(self):
        self.assertEqual(self.watch([("idle", 200)], ("t1", "declined")), 0)
        self.assertEqual(self.watch([("busy", 200)], ("t1", "declined")), 0)

    def test_permission_prompt_keeps_the_focus(self):
        self.assertEqual(self.watch([("busy", 200)], ("t0", "answered")), 0)

    def test_the_user_left_the_pane(self):
        self.assertEqual(self.watch([("busy", 200)], ("t1", "answered"), focus=False), 0)

    def test_session_ended(self):
        self.assertEqual(self.watch([OPEN, None], ("t1", "answered")), 0)


if __name__ == "__main__":
    unittest.main()
