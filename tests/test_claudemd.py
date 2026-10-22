import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import claudemd, cli, store  # noqa: E402
from claude_focus import pending as pend  # noqa: E402

CHANGED = 1792667770000  # 2026-10-22 13:16:10: the rule about waiting for other people


class NoticeTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        patches = {"DIR": d, "STATE": os.path.join(d, "state.json"), "LOCK": os.path.join(d, ".lock"),
                   "LOG": os.path.join(d, "focus.log"), "CONFIG": os.path.join(d, "config.json")}
        for name, value in patches.items():
            p = mock.patch.object(store, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.path = os.path.join(d, "CLAUDE.md")
        self.change(CHANGED)
        own = mock.patch.object(pend, "own_session",
                                return_value={"pid": 7, "sessionId": "s1", "name": "zupnijska-stran",
                                              "startedAt": CHANGED - 16 * 3600000})
        self.own = own.start()
        self.addCleanup(own.stop)

    def change(self, ms):
        with open(self.path, "w") as f:
            f.write("# rules\n")
        os.utime(self.path, (ms / 1000, ms / 1000))

    def notice(self, sid="s1"):
        return claudemd.notice({"session_id": sid, "prompt": "hi"}, self.path)

    def test_a_session_that_started_before_the_change_is_told_once(self):
        self.assertIn("changed at 2026-10-22 13:16, after this session started", self.notice())
        self.assertIsNone(self.notice())
        self.change(CHANGED + 3600000)  # changed again: told again
        self.assertIn("changed at 2026-10-22 14:16", self.notice())

    def test_a_session_that_started_after_the_change_read_it(self):
        self.own.return_value = dict(self.own.return_value, startedAt=CHANGED + 60000)
        self.assertIsNone(self.notice())
        self.own.reset_mock()
        self.assertIsNone(self.notice())
        self.own.assert_not_called()  # checked once per version: later prompts cost one stat

    def test_only_the_current_versions_entries_are_kept(self):
        self.notice("s1")
        self.change(CHANGED + 3600000)
        self.own.return_value = dict(self.own.return_value, sessionId="s2")
        self.notice("s2")
        self.assertEqual(store.load_state()["claude_md"], {"s2": CHANGED + 3600000})

    def test_off_in_the_config(self):
        with mock.patch.object(store, "load_config", return_value=dict(store.DEFAULT_CONFIG, claude_md_notice=False)):
            self.assertIsNone(self.notice())

    def test_the_hook_adds_it_to_the_prompt(self):
        out = io.StringIO()
        with mock.patch.object(claudemd, "PATH", self.path), \
                mock.patch("sys.stdin",io.StringIO(json.dumps({"session_id": "s1", "prompt": "hi"}))), \
                contextlib.redirect_stdout(out):
            cli.cmd_hook(["prompt-submit"])
        context = json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith("[claude-focus] "))
        self.assertIn("Read the file again now", context)
