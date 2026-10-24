"""The poll starts as soon as a session's status changes: kqueue on each
session file (Claude Code writes them in place), and the engine logs each
status change. jev scores a status after 4 s, the Claude scorer after 15 s."""
import json
import os
import queue
import tempfile
import time
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402
from claude_focus import store, watch  # noqa: E402


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.dir, self.other = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.file = os.path.join(self.dir, "101.json")
        self.write(self.file, "idle")
        self.calls = queue.Queue()
        self.assertTrue(watch.start(self.calls.put, self.dir, (self.other,)))
        time.sleep(0.2)  # the thread registers the files

    def write(self, path, status):
        with open(path, "w") as f:  # in place, as Claude Code does
            json.dump({"status": status}, f)

    def next_call(self):
        return self.calls.get(timeout=2)

    def test_a_status_written_in_place(self):
        self.write(self.file, "busy")
        self.assertEqual(self.next_call(), self.dir)

    def test_a_new_session_file_and_its_writes(self):
        new = os.path.join(self.dir, "202.json")
        self.write(new, "busy")
        self.assertEqual(self.next_call(), self.dir)
        time.sleep(0.2)
        while not self.calls.empty():
            self.calls.get()
        self.write(new, "idle")
        self.assertEqual(self.next_call(), self.dir)

    def test_a_file_replaced_in_another_folder(self):
        tmp = os.path.join(self.other, "state.json.tmp")
        self.write(tmp, "scored")
        os.replace(tmp, os.path.join(self.other, "state.json"))
        self.assertEqual(self.next_call(), self.other)


class StatusLogTest(unittest.TestCase):
    def lines(self, before, after):
        with mock.patch.object(store, "log") as log:
            appmod.log_status_changes(before, after)
        return [c.args[0] for c in log.call_args_list]

    def test_changes_starts_and_ends(self):
        a = {"key": "a", "name": "personal-domen", "status": "busy"}
        b = {"key": "b", "name": "linkmcp", "status": "shell"}
        c = {"key": "c", "name": "goldmine", "status": "idle"}
        self.assertEqual(self.lines([a, b], [dict(a, status="idle"), b, c]),
                         ["status personal-domen: busy -> idle", "status goldmine: started, idle"])
        self.assertEqual(self.lines([a, b], [a]), ["status linkmcp: ended"])

    def test_nothing_on_the_first_poll(self):
        self.assertEqual(self.lines([], [{"key": "a", "name": "x", "status": "idle"}]), [])


class StableTest(unittest.TestCase):
    def scheduled(self, scorer, idle_s):
        app = appmod.App(None)
        app.cfg = dict(app.cfg, scorer=scorer)
        app.bridge = mock.Mock(pane_of=lambda s: None, user_pane=lambda: None)
        app.sessions = [{"key": "a", "sessionId": "a", "name": "x", "status": "idle", "pid": 1,
                         "statusSince": time.time() * 1000 - idle_s * 1000}]
        with mock.patch.object(appmod.asyncio, "ensure_future") as fut, \
                mock.patch.object(app, "_score", mock.Mock()):
            app._schedule_scores({"sessions": {}, "pending": {}})
        return fut.called

    def test_jev_scores_after_4_s(self):
        self.assertFalse(self.scheduled("jev", 2))
        self.assertTrue(self.scheduled("jev", 5))

    def test_the_claude_scorer_waits_15_s(self):
        self.assertFalse(self.scheduled("claude", 5))
        self.assertTrue(self.scheduled("claude", 16))


if __name__ == "__main__":
    unittest.main()
