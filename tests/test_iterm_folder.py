"""A free terminal's folder comes from its foreground process: iTerm2's path
variable still named the project after the user typed cd in the shell that
Claude Code had ended in (ttys015 on 2026-11-01)."""
import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import iterm  # noqa: E402


class FolderTest(unittest.TestCase):
    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp())  # /var is /private/var
        self.shell = subprocess.Popen(["sleep", "30"], cwd=self.home)
        self.addCleanup(self.shell.kill)

    def test_a_process_folder(self):
        self.assertEqual(iterm.proc_cwd(self.shell.pid), self.home)

    def test_no_process(self):
        self.assertIsNone(iterm.proc_cwd(None))
        self.shell.kill()
        self.shell.wait()
        self.assertIsNone(iterm.proc_cwd(self.shell.pid))

    def test_pane_info_takes_the_process_folder(self):
        b = iterm.Bridge(None)
        b._known = {"s1": {"tty": "ttys015", "tab_id": "t"}}
        values = ["Available", "/Users/x/Projects/personal-subtitle-translator", "zsh", self.shell.pid]
        reply = NS(variable_response=NS(values=[json.dumps(v) for v in values]))
        with mock.patch.object(iterm.iterm2.rpc, "async_variable", mock.AsyncMock(return_value=reply)):
            info = asyncio.run(b.pane_info())
        self.assertEqual(info, [("s1", "ttys015", "Available", self.home, "zsh")])


class SessionProfileTest(unittest.TestCase):
    """claude-focus opens its windows with the session profile: iTerm2's
    default can be the green free profile (Cmd+N windows free, 2026-11-07)."""
    def test_the_session_profile(self):
        create = mock.AsyncMock(return_value="window")
        with mock.patch.object(iterm.store, "load_config", return_value={"session_profile": "Default"}):
            self.assertEqual(asyncio.run(iterm.with_session_profile(create, command="x")), "window")
        create.assert_awaited_once_with(profile="Default", command="x")

    def test_no_such_profile(self):
        create = mock.AsyncMock(side_effect=[iterm.iterm2.CreateWindowException("INVALID_PROFILE_NAME"), "window"])
        with mock.patch.object(iterm.store, "load_config", return_value={"session_profile": "Gone"}):
            self.assertEqual(asyncio.run(iterm.with_session_profile(create)), "window")
        self.assertEqual(create.await_args_list[-1], mock.call())


if __name__ == "__main__":
    unittest.main()
