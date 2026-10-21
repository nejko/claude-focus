"""The engine keeps the green color true: a pane whose Claude session ended
is free (profile Available), a free pane in which a session starts is not."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402
from claude_focus import iterm, store  # noqa: E402


class RecolorTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        p = mock.patch.object(store, "LOG", os.path.join(d, "focus.log"))
        p.start()
        self.addCleanup(p.stop)
        self.app = appmod.App(None)
        self.app.bridge = iterm.Bridge(None)
        self.app.bridge.panes = {"ttys001": "p1"}  # tty -> pane, as refresh() finds them
        self.app.bridge.set_profile = mock.AsyncMock(return_value=True)
        self.panes = {"p1": ["ttys001", "Default", "~", "2.1.304"]}
        self.app.bridge.pane_info = mock.AsyncMock(
            side_effect=lambda: [(sid, *v) for sid, v in self.panes.items()])
        self.session = {"pid": 7, "sessionId": "abc", "key": "abc", "name": "backend", "tty": "ttys001"}
        self.app.sessions = [self.session]
        self.now = 1000.0

    def tick(self, seconds=3):
        self.now += seconds
        with mock.patch.object(appmod.time, "time", return_value=self.now):
            asyncio.run(self.app._recolor_panes())
        calls = [c.args for c in self.app.bridge.set_profile.await_args_list]
        self.app.bridge.set_profile.reset_mock()
        return calls

    def end_session(self, job="zsh"):
        self.app.sessions = []
        self.panes["p1"][3] = job

    def test_a_session_that_ends_frees_its_pane(self):
        self.assertEqual(self.tick(), [])
        self.end_session()
        self.assertEqual(self.tick(), [("p1", "Available")])
        self.panes["p1"][1] = "Available"
        self.assertEqual(self.tick(), [])

    def test_a_session_in_a_free_pane_takes_it(self):
        self.panes["p1"][1] = "Available"
        self.assertEqual(self.tick(), [("p1", "Default")])

    def test_the_pane_runs_another_program_after_the_session(self):
        self.tick()
        self.end_session(job="vim")
        self.assertEqual(self.tick(), [])
        self.assertEqual(self.tick(appmod.ENDED_GRACE + 1), [])  # forgotten
        self.panes["p1"][3] = "zsh"
        self.assertEqual(self.tick(), [])  # the user quit vim later: not freed by itself

    def test_an_unreadable_session_file_while_claude_runs(self):
        self.tick()
        self.app.sessions = []  # Claude Code rewrites the file; the pane still runs claude
        self.assertEqual(self.tick(), [])
        self.app.sessions = [self.session]
        self.assertEqual(self.tick(), [])

    def test_a_pane_that_never_ran_a_session_stays_as_it_is(self):
        self.app.sessions = []
        self.panes["p1"][3] = "zsh"
        self.assertEqual(self.tick(), [])

    def test_a_pane_already_green_when_the_session_ends(self):
        self.tick()
        self.end_session()
        self.panes["p1"][1] = "Available"  # the user pressed their key before /exit
        self.assertEqual(self.tick(), [])

    def test_login_shell_name(self):
        self.tick()
        self.end_session(job="-zsh")
        self.assertEqual(self.tick(), [("p1", "Available")])


if __name__ == "__main__":
    unittest.main()
