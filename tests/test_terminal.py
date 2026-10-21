"""Which terminal claude-focus drives (terminal.detect)."""
import os
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import herdr, terminal  # noqa: E402

# Two Claude sessions in herdr panes, one in an iTerm2 pane, one in Terminal.app.
PS = """\
    1     0 /sbin/launchd
   20     1 /Users/ana/.nix-profile/bin/herdr
  100    20 -zsh
  101   100 claude
  110    20 -zsh
  111   110 claude
   50     1 /Applications/iTerm.app/Contents/MacOS/iTerm2
   51    50 /Users/ana/Library/Application Support/iTerm2/iTermServer-3.5.14
   52    51 /usr/bin/login
   53    52 -zsh
  201    53 claude
   70     1 /System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal
   71    70 -zsh
  301    71 claude
"""


def env(**values):
    """os.environ without any terminal's variables, plus values."""
    out = {k: v for k, v in os.environ.items() if not k.startswith(("HERDR_", "ITERM_"))}
    out.update(values)
    return out


class WhereTest(unittest.TestCase):
    def test_by_parent_processes(self):
        self.assertEqual(terminal.where_sessions_run([101, 111, 201, 301, 999], PS), {"herdr": 2, "iterm": 1})


class DetectTest(unittest.TestCase):
    def detect(self, environ, cfg=None, herdr_up=True, iterm_up=True, counts=None, current=None):
        with mock.patch.dict(os.environ, environ, clear=True), \
                mock.patch.object(herdr, "ping", return_value={"version": "0.9.5"} if herdr_up else None), \
                mock.patch.object(terminal, "iterm_running", return_value=iterm_up), \
                mock.patch.object(terminal, "where_sessions_run", return_value=counts or {"herdr": 0, "iterm": 0}), \
                mock.patch("claude_focus.sessions.list_sessions", return_value=[]):
            return terminal.detect(cfg or {"terminal": "auto"}, current)

    def test_the_config_decides_first(self):
        self.assertEqual(self.detect(env(HERDR_PANE_ID="w1:p1"), {"terminal": "iterm"}), "iterm")

    def test_inside_a_pane(self):
        self.assertEqual(self.detect(env(ITERM_SESSION_ID="w0t0p0:ABC")), "iterm")
        self.assertEqual(self.detect(env(HERDR_PANE_ID="w1:p1")), "herdr")
        # herdr's panes can inherit ITERM_SESSION_ID from the iTerm2 that started its server
        self.assertEqual(self.detect(env(HERDR_PANE_ID="w1:p1", ITERM_SESSION_ID="w0t0p0:ABC")), "herdr")
        self.assertEqual(self.detect(env(HERDR_SOCKET_PATH="/x/herdr.sock")), "herdr")  # a herdr key binding

    def test_the_service_follows_the_sessions(self):
        self.assertEqual(self.detect(env(), counts={"herdr": 1, "iterm": 4}), "iterm")
        self.assertEqual(self.detect(env(), counts={"herdr": 4, "iterm": 1}), "herdr")
        self.assertEqual(self.detect(env(), counts={"herdr": 0, "iterm": 0}), "herdr")

    def test_a_tie_keeps_the_service_where_it_runs(self):
        # Without this, the service restarted on the other terminal every minute while the counts tied.
        for counts in ({"herdr": 0, "iterm": 0}, {"herdr": 3, "iterm": 3}):
            self.assertEqual(self.detect(env(), counts=counts, current="iterm"), "iterm")
            self.assertEqual(self.detect(env(), counts=counts, current="herdr"), "herdr")
        self.assertEqual(self.detect(env(), counts={"herdr": 1, "iterm": 4}, current="herdr"), "iterm")

    def test_whichever_runs(self):
        self.assertEqual(self.detect(env(), herdr_up=False), "iterm")
        self.assertEqual(self.detect(env(), iterm_up=False), "herdr")
        self.assertIsNone(self.detect(env(), herdr_up=False, iterm_up=False))


class SocketTest(unittest.TestCase):
    def test_config_then_environment_then_default(self):
        with mock.patch.dict(os.environ, env(HERDR_SOCKET_PATH="/run/h.sock"), clear=True):
            self.assertEqual(herdr.socket_path({"herdr_socket": "~/x.sock"}), os.path.expanduser("~/x.sock"))
            self.assertEqual(herdr.socket_path({"herdr_socket": ""}), "/run/h.sock")
        with mock.patch.dict(os.environ, env(), clear=True):
            self.assertEqual(herdr.socket_path({}), herdr.DEFAULT_SOCKET)


if __name__ == "__main__":
    unittest.main()
