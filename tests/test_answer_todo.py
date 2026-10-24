import asyncio
import contextlib
import datetime as dt
import io
import os
import tempfile
import time
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402
from claude_focus import cli  # noqa: E402
from claude_focus import pending as pend  # noqa: E402
from claude_focus import sessions as sessions_mod  # noqa: E402
from claude_focus import store  # noqa: E402

STAMP = int(dt.datetime(2026, 10, 14, 6, 32, 7).timestamp() * 1000)  # blog-posting-a6 ended its turn
SESS = {"pid": 47096, "sessionId": "s1", "name": "blog-posting-a6", "cwd": "/x"}


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        names = {"DIR": d, "STATE": os.path.join(d, "state.json"), "LOCK": os.path.join(d, ".lock"),
                 "LOG": os.path.join(d, "focus.log"), "PRIORITIES": os.path.join(d, "p.md")}
        for name, value in names.items():
            p = mock.patch.object(store, name, value)
            p.start()
            self.addCleanup(p.stop)

    def record(self):
        return store.load_state()["pending"]["s1"]


class PendingCommandTest(Base):
    def run_pending(self, answer, *extra):
        out = io.StringIO()
        with mock.patch.object(pend, "own_session", return_value=SESS), \
                mock.patch.object(pend, "app_running", return_value=True), \
                mock.patch.object(sessions_mod, "last_user_answer", return_value=answer), \
                contextlib.redirect_stdout(out):
            cli.cmd_pending(["--until", "+2h", "--note", "fit checks", *extra])
        return out.getvalue()

    def todo(self):
        return self.record()["needs"], self.record()["needs_auto"]

    def test_wait_in_a_turn_that_answers_the_user_gets_the_read_todo(self):
        out = self.run_pending("running")
        self.assertEqual(self.todo(), (pend.READ_ANSWER, True))
        self.assertIn("this turn answers a message from the user", out)

    def test_own_needs_wins(self):
        self.run_pending("running", "--needs", "Send the draft")
        self.assertEqual(self.todo(), ("Send the draft", False))

    def test_turn_without_his_message_leaves_the_queue(self):
        out = self.run_pending(None)
        self.assertEqual(self.todo(), (None, False))
        self.assertIn("leaves the user's queue", out)

    def test_a_new_wait_keeps_the_open_todo_of_the_wait_it_replaces(self):
        # A session on 2026-10-15 09:15: a Monitor expired and
        # started a turn, and the session set its wait again in that turn
        self.run_pending("running")
        out = self.run_pending(STAMP)
        self.assertEqual(self.todo(), (pend.READ_ANSWER, True))
        self.assertIn("has not looked at your answer", out)
        store.update_state(lambda st: st["pending"]["s1"].__setitem__("needs_done", True))
        self.run_pending(STAMP)
        self.assertEqual(self.todo(), (None, False))  # they saw it before (no "seen" record: tracking off)

    def test_answer_from_a_turn_before_the_turns_of_background_tasks(self):
        store.update_state(lambda st: st.update(seen_since=STAMP - 3600000, seen={"s1": STAMP - 60000}))
        out = self.run_pending(STAMP)  # answered at STAMP; the user was in the pane last a minute before
        self.assertEqual(self.todo(), (pend.READ_ANSWER, True))
        self.assertIn("has not looked at this session since your answer", out)
        pend.clear_pending("s1")
        store.update_state(lambda st: st["seen"].__setitem__("s1", STAMP + 5000))
        self.run_pending(STAMP)
        self.assertEqual(self.todo(), (None, False))

    def test_answer_from_before_the_seen_record_started(self):
        store.update_state(lambda st: st.update(seen_since=STAMP + 1, seen={}))
        self.run_pending(STAMP)
        self.assertEqual(self.todo(), (None, False))


class CheckTriedTest(Base):
    """A check is run once when the wait is set: one that already succeeds
    would wake the session at once, after every turn (linkmcp, 2026-10-24)."""
    def run_check(self, check):
        sess = dict(SESS, cwd=tempfile.mkdtemp())
        with mock.patch.object(pend, "own_session", return_value=sess), \
                mock.patch.object(pend, "app_running", return_value=True), \
                mock.patch.object(sessions_mod, "last_user_answer", return_value=None), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_pending(["--check", check, "--note", "mail"])

    def test_a_check_that_succeeds_now_sets_no_wait(self):
        with self.assertRaises(SystemExit) as e:
            self.run_check("test -d .")
        self.assertIn("succeeds already now", str(e.exception.code))
        self.assertNotIn("s1", store.load_state().get("pending", {}))

    def test_a_check_that_fails_now_sets_the_wait(self):
        self.run_check("test -f new-mail")
        self.assertEqual(self.record()["check"], "test -f new-mail")

    def test_a_slow_check_sets_the_wait_untried(self):
        with mock.patch.object(pend, "TRY_TIMEOUT", 0.2):
            self.run_check("sleep 2")
        self.assertEqual(self.record()["check"], "sleep 2")


class AnswerSeenTest(Base):
    def tick(self, user_in, status="idle"):
        app = appmod.App(None)
        app.sessions = [dict(SESS, key="s1", status=status, statusSince=STAMP)]
        app._user_in = lambda s: user_in
        asyncio.run(app.pending_tick(store.load_state()))
        return self.record()

    def setUp(self):
        super().setUp()
        pend.set_pending(SESS, int(time.time() * 1000) + 86400000, None, 5, "fit checks", pend.READ_ANSWER, needs_auto=True)

    def test_done_when_the_user_is_in_the_pane_as_the_turn_ends(self):
        self.assertTrue(self.tick(user_in=True)["needs_done"])

    def test_stays_a_todo_until_he_looks(self):
        self.assertFalse(self.tick(user_in=False)["needs_done"])  # the turn ends: armed
        self.assertFalse(self.tick(user_in=False)["needs_done"])
        self.assertTrue(self.tick(user_in=True)["needs_done"])

    def test_a_todo_that_the_session_set_itself_needs_d(self):
        store.update_state(lambda st: st["pending"]["s1"].update(needs="Send the draft", needs_auto=False))
        self.assertFalse(self.tick(user_in=True)["needs_done"])


class NoteSeenTest(Base):
    def test_when_the_user_was_last_in_each_pane(self):
        app = appmod.App(None)
        app.sessions = [dict(SESS, key="s1"), dict(SESS, sessionId="s2", key="s2", pid=2)]
        where = {"pane": "s1"}
        app._user_in = lambda s: s["sessionId"] == where["pane"]

        def at(seconds, pane):
            where["pane"] = pane
            with mock.patch.object(appmod.time, "time", return_value=seconds):
                app._note_seen()
            return store.load_state().get("seen")

        self.assertEqual(at(1000, "s1"), {"s1": 1000000})
        self.assertEqual(store.load_state()["seen_since"], 1000000)
        self.assertEqual(at(1010, "s1"), {"s1": 1000000})  # at most one write in 30 s
        self.assertEqual(at(1040, "s1"), {"s1": 1040000})
        self.assertEqual(at(1045, "s2"), {"s1": 1045000, "s2": 1045000})  # they left s1 just now
        self.assertEqual(at(1050, None), {"s1": 1045000, "s2": 1050000})
        self.assertEqual(at(1200, None), {"s1": 1045000, "s2": 1050000})
        self.assertEqual(store.load_state()["seen_since"], 1000000)


if __name__ == "__main__":
    unittest.main()
