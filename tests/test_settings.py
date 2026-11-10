"""The settings screen (i): a switch turns over, a choice moves on, a text
is typed; each change goes into config.json, and a running engine reads
the changed file."""
import asyncio
import os
import time
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402
from claude_focus import store  # noqa: E402


class SettingsTest(unittest.TestCase):
    def setUp(self):
        if os.path.exists(store.CONFIG):
            os.remove(store.CONFIG)
        self.app = appmod.App(None)
        self.app.bridge = mock.Mock()
        self.keys("i")

    def keys(self, *keys):
        for k in keys:
            asyncio.run(self.app.on_key(k))

    def at(self, name):
        self.app.set_sel = [k for k, _, _ in appmod.SETTINGS].index(name)

    def test_a_switch(self):
        self.assertEqual(self.app.mode, "settings")
        self.at("confirm_close")
        self.keys("\r")
        self.assertIs(store.load_config()["confirm_close"], False)
        self.assertIs(self.app.cfg["confirm_close"], False)
        self.keys(" ")
        self.assertIs(store.load_config()["confirm_close"], True)

    def test_a_choice(self):
        self.at("history_model")
        self.keys("\r")
        self.assertEqual(store.load_config()["history_model"], "")  # off
        self.keys("\r")
        self.assertEqual(store.load_config()["history_model"], "claude-haiku-5-5")

    def test_a_text(self):
        self.at("user_name")
        self.keys("\r", "\x15", "A", "n", "a", "\r")
        self.assertEqual(store.load_config()["user_name"], "Ana")
        self.keys("\r", "X", "\x1b")  # Esc: typing ends, nothing saved
        self.assertEqual(store.load_config()["user_name"], "Ana")
        self.assertEqual(self.app.mode, "settings")

    def test_other_settings_stay(self):
        store.set_config("project_roots", ["~/work"])
        self.at("zoom")
        self.keys("\r")
        self.assertEqual(store.load_config()["project_roots"], ["~/work"])

    def test_esc_goes_back(self):
        self.keys("\x1b")
        self.assertEqual(self.app.mode, "main")

    def test_the_engine_reads_a_changed_file(self):
        other = appmod.App(None)  # the service, with the config of its start
        time.sleep(0.01)
        store.set_config("confirm_close", False)
        os.utime(store.CONFIG, (time.time() + 5, time.time() + 5))  # a new mtime, also on a coarse clock
        other._reload_config()
        self.assertIs(other.cfg["confirm_close"], False)

    def test_the_screen_shows_every_setting(self):
        self.app.pal = appmod.look.DARK
        text = "\n".join(self.app._settings_lines(160))
        for key, _, _ in appmod.SETTINGS:
            self.assertIn(key, text)


if __name__ == "__main__":
    unittest.main()
