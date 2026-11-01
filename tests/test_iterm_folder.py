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


if __name__ == "__main__":
    unittest.main()
