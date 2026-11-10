"""A new release: how this copy was installed, when PyPI is asked, what the
focus screen offers, and what U runs."""
import subprocess
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import store, update  # noqa: E402


class KindTest(unittest.TestCase):
    def test_install_kinds(self):
        self.assertEqual(update.install_kind(), "checkout")  # these tests run in the git checkout
        for path, kind in (
                ("/opt/homebrew/Cellar/claude-focus/0.1.5/libexec/lib/python3.15/site-packages/claude_focus", "brew"),
                ("/Users/a/.local/share/uv/tools/claude-focus/lib/python3.12/site-packages/claude_focus", "uv"),
                ("/Users/a/.local/pipx/venvs/claude-focus/lib/python3.12/site-packages/claude_focus", "pipx"),
                ("/usr/lib/python3/site-packages/claude_focus", "pip")):
            self.assertEqual(update.install_kind(path), kind, path)


class CheckTest(unittest.TestCase):
    def setUp(self):
        store.update_state(lambda st: st.pop("update", None))

    def test_versions(self):
        self.assertTrue(update.newer("0.1.10", "0.1.9"))
        self.assertFalse(update.newer("0.1.4", "0.1.4"))
        self.assertFalse(update.newer("garbage", "0.1.4"))

    def test_once_a_day(self):
        self.assertTrue(update.due({}))
        st = {"update": {"checked_at": 1000 * 3600 * 100}}
        self.assertFalse(update.due(st, now=3600 * 110))
        self.assertTrue(update.due(st, now=3600 * 125))

    def test_a_newer_version_is_offered(self):
        with mock.patch.object(update, "latest", return_value="9.0.0"):
            self.assertEqual(update.check("brew"), "9.0.0")
        st = store.load_state()
        self.assertEqual(update.available(st, "brew"), "9.0.0")
        self.assertIsNone(update.available(st, "checkout"))  # its owner pulls
        with mock.patch.object(update, "latest", return_value=update.__version__):
            update.check("uv")
        self.assertIsNone(update.available(store.load_state(), "uv"))

    def test_a_checkout_does_not_ask(self):
        with mock.patch.object(update, "latest") as latest:
            self.assertIsNone(update.check("checkout"))
        latest.assert_not_called()

    def test_offline(self):
        with mock.patch.object(update.urllib.request, "urlopen", side_effect=OSError("no network")):
            self.assertIsNone(update.latest())


class RunTest(unittest.TestCase):
    def done(self, code=0, out=""):
        return subprocess.CompletedProcess([], code, out, "")

    def test_brew_then_setup(self):
        with mock.patch.object(update.subprocess, "run", side_effect=[self.done()] * 3) as run, \
                mock.patch.object(update, "installed", return_value="9.0.0"):
            ok, _ = update.run("brew", say=lambda t: None)
        self.assertTrue(ok)
        self.assertEqual([c.args[0] for c in run.call_args_list],  # brew update first: the tap is refreshed only now and then
                         [["brew", "update"], ["brew", "upgrade", "claude-focus"], ["claude-focus", "setup", "--yes"]])

    def test_uv_takes_the_latest_over_a_pin(self):
        with mock.patch.object(update.subprocess, "run", side_effect=[self.done()] * 2) as run, \
                mock.patch.object(update, "installed", return_value="9.0.0"):
            update.run("uv", say=lambda t: None)
        self.assertEqual(run.call_args_list[0].args[0], ["uv", "tool", "install", "claude-focus@latest"])

    def test_nothing_newer_yet(self):
        # The Homebrew formula comes after the PyPI release, by hand.
        with mock.patch.object(update.subprocess, "run", return_value=self.done()), \
                mock.patch.object(update, "installed", return_value=update.__version__):
            ok, text = update.run("brew", say=lambda t: None)
        self.assertFalse(ok)
        self.assertIn("nothing newer", text)

    def test_a_failed_upgrade(self):
        with mock.patch.object(update.subprocess, "run", return_value=self.done(1, "Error: no network")):
            ok, text = update.run("uv", say=lambda t: None)
        self.assertFalse(ok)
        self.assertIn("no network", text)

    def test_no_command_for_a_checkout_or_pip(self):
        with mock.patch.object(update.subprocess, "run") as run:
            self.assertFalse(update.run("checkout", say=lambda t: None)[0])
            self.assertIn("pip install -U", update.run("pip", say=lambda t: None)[1])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
