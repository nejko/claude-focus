import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import cli  # noqa: E402
from claude_focus import setup as su  # noqa: E402
from claude_focus import store  # noqa: E402

RULE = "# Waiting on something external: claude-focus pending\n\nRun claude-focus pending ...\n"


class HookTest(unittest.TestCase):
    def test_add_once(self):
        settings = {"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say hi"}]}]}}
        self.assertTrue(su.add_hook(settings, "/bin/claude-focus hook prompt-submit"))
        self.assertFalse(su.add_hook(settings, "/other/claude-focus hook prompt-submit"))
        groups = settings["hooks"]["UserPromptSubmit"]
        self.assertEqual(groups, [{"hooks": [{"type": "command", "command": "/bin/claude-focus hook prompt-submit",
                                              "timeout": 10}]}])
        self.assertIn("Stop", settings["hooks"])

    def test_remove_keeps_other_hooks(self):
        settings = {"hooks": {"UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "log-prompt"},
                       {"type": "command", "command": "/x/claude-focus hook prompt-submit"}]}]}}
        self.assertTrue(su.remove_hook(settings))
        self.assertEqual(settings["hooks"]["UserPromptSubmit"], [{"hooks": [{"type": "command", "command": "log-prompt"}]}])
        self.assertFalse(su.remove_hook(settings))

    def test_remove_the_only_hook_leaves_no_empty_keys(self):
        settings = {"model": "x"}
        su.add_hook(settings, "claude-focus hook prompt-submit")
        su.remove_hook(settings)
        self.assertEqual(settings, {"model": "x"})


class RuleTest(unittest.TestCase):
    def test_add_and_remove_restore_the_text(self):
        text = "# My rules\n\nBe brief.\n"
        added, changed = su.add_rule(text, RULE)
        self.assertTrue(changed)
        self.assertIn(su.MARK_START, added)
        self.assertEqual(su.add_rule(added, RULE), (added, False))
        self.assertEqual(su.remove_rule(added), (text, True))

    def test_an_own_rule_is_kept(self):
        own = "Run `claude-focus pending --until` when you wait.\n"
        self.assertEqual(su.add_rule(own, RULE), (own, False))
        self.assertEqual(su.remove_rule(own), (own, False))

    def test_empty_file(self):
        added, _ = su.add_rule("", RULE)
        self.assertTrue(added.startswith(su.MARK_START))
        self.assertEqual(su.remove_rule(added), ("", True))

    def test_block_in_the_middle(self):
        added, _ = su.add_rule("top\n", RULE)
        text = added + "\n## Later rules\n"
        self.assertEqual(su.remove_rule(text)[0], "top\n\n## Later rules\n")


class LastingPathTest(unittest.TestCase):
    def test_a_cellar_path_becomes_the_opt_link(self):
        with tempfile.TemporaryDirectory() as brew:
            os.makedirs(f"{brew}/Cellar/claude-focus/0.1.0/libexec/bin")
            os.makedirs(f"{brew}/opt")
            os.symlink(f"{brew}/Cellar/claude-focus/0.1.0", f"{brew}/opt/claude-focus")
            self.assertEqual(cli.lasting(f"{brew}/Cellar/claude-focus/0.1.0/libexec/bin/python"),
                             f"{brew}/opt/claude-focus/libexec/bin/python")

    def test_other_paths_stay(self):
        for path in ("/Users/me/.local/share/uv/tools/claude-focus/bin/python",
                     "/nowhere/Cellar/claude-focus/0.1.0/libexec/bin/python",  # no opt link there
                     "/opt/homebrew/Cellar/claude-focus"):
            self.assertEqual(cli.lasting(path), path)


class ProfileTest(unittest.TestCase):
    def test_green_profile_with_both_themes(self):
        p = su.profile_plist("Available")["Profiles"][0]
        self.assertEqual((p["Name"], p["Guid"]), ("Available", su.PROFILE_GUID))
        dark = p["Background Color (Dark)"]
        self.assertGreater(dark["Green Component"], dark["Red Component"])
        self.assertIn("Background Color (Light)", p)
        self.assertNotIn("Dynamic Profile Parent Name", p)  # inherits the default profile


class SetupStepsTest(unittest.TestCase):
    """The steps with every path in a scratch folder and no iTerm2 or launchd."""

    def setUp(self):
        d = tempfile.mkdtemp()
        self.d = d
        paths = {"SETTINGS": os.path.join(d, "claude", "settings.json"),
                 "CLAUDE_MD": os.path.join(d, "claude", "CLAUDE.md"),
                 "PROFILE_FILE": os.path.join(d, "DynamicProfiles", "claude-focus.json"),
                 "HERDR_CONFIG": os.path.join(d, "herdr", "config.toml")}
        for name, value in paths.items():
            p = mock.patch.object(su, name, value)
            p.start()
            self.addCleanup(p.stop)
        for name, value in {"CONFIG": os.path.join(d, "config.json"), "DIR": d}.items():
            p = mock.patch.object(store, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(su, "command_path", return_value="/bin/claude-focus")
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(su, "_wait_for_profile", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        os.makedirs(os.path.join(d, "claude"))
        with open(su.SETTINGS, "w") as f:
            json.dump({"model": "opus"}, f)
        with open(su.CLAUDE_MD, "w") as f:
            f.write("# Mine\n")

    def run_steps(self, dry_run=False, profiles=()):
        s = su.Setup(yes=True, dry_run=dry_run)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            s.config()
            s.hook()
            s.rule()
            s.free_terminals({"profiles": list(profiles), "default_profile": "Main"})
        return out.getvalue()

    def files(self):
        out = {}
        for path in (su.SETTINGS, su.CLAUDE_MD, su.PROFILE_FILE, store.CONFIG):
            out[path] = open(path).read() if os.path.exists(path) else None
        return out

    def test_setup_then_again_changes_nothing(self):
        with mock.patch.object(su, "first_name", return_value="Ana"):
            self.run_steps()
        first = self.files()
        self.assertEqual(json.loads(first[store.CONFIG]),
                         {"user_name": "Ana", "auto_profiles": True, "session_profile": "Main"})
        self.assertIn("claude-focus hook prompt-submit", first[su.SETTINGS])
        self.assertIn("claude-focus pending", first[su.CLAUDE_MD])
        self.assertEqual(json.loads(first[su.PROFILE_FILE])["Profiles"][0]["Name"], "Available")
        self.assertTrue(os.path.exists(su.SETTINGS + su.BACKUP))
        out = self.run_steps(profiles=["Available"])
        self.assertEqual(self.files(), first)
        self.assertNotIn("+ ", out)

    def test_dry_run_writes_nothing(self):
        before = self.files()
        out = self.run_steps(dry_run=True)
        self.assertEqual(self.files(), before)
        self.assertIn("would add the prompt hook", out)

    def test_an_existing_profile_is_not_replaced(self):
        self.run_steps(profiles=["Available"])
        self.assertIsNone(self.files()[su.PROFILE_FILE])

    def test_uninstall_removes_what_setup_added(self):
        self.run_steps()
        with mock.patch.object(cli, "cmd_uninstall_daemon"), contextlib.redirect_stdout(io.StringIO()):
            su.uninstall()
        files = self.files()
        self.assertEqual(json.loads(files[su.SETTINGS]), {"model": "opus"})
        self.assertEqual(files[su.CLAUDE_MD], "# Mine\n")
        self.assertIsNone(files[su.PROFILE_FILE])
        self.assertIsNotNone(files[store.CONFIG])  # kept without --purge

    def herdr_keys(self, answers=None):
        s = su.Setup(yes=True)
        out = io.StringIO()
        reply = lambda path, method, params=None: answers or {}  # noqa: E731
        with mock.patch.object(su.herdr, "call", side_effect=reply) as call, contextlib.redirect_stdout(out):
            s.herdr_keys("/x/herdr.sock")
        return out.getvalue(), [c.args[1] for c in call.call_args_list]

    def test_herdr_keys_then_again_then_uninstall(self):
        os.makedirs(os.path.dirname(su.HERDR_CONFIG))
        mine = '[ui]\naccent = "#eb3f6c"\n\n[keys]\ndetach = ["prefix+q", "prefix+d"]\n'
        with open(su.HERDR_CONFIG, "w") as f:
            f.write(mine)
        out, calls = self.herdr_keys()
        self.assertEqual(calls, ["server.reload_config"])
        text = open(su.HERDR_CONFIG).read()
        self.assertTrue(text.startswith(mine))
        self.assertIn('key = "prefix+a"\ntype = "shell"\ncommand = "/bin/claude-focus go"', text)
        self.assertIn('key = "prefix+f"\ntype = "shell"\ncommand = "/bin/claude-focus show"', text)
        out, calls = self.herdr_keys()
        self.assertEqual((open(su.HERDR_CONFIG).read(), calls), (text, []))
        self.assertIn("✓ herdr keys", out)
        with mock.patch.object(cli, "cmd_uninstall_daemon"), mock.patch.object(su.herdr, "call"), \
                contextlib.redirect_stdout(io.StringIO()):
            su.uninstall()
        self.assertEqual(open(su.HERDR_CONFIG).read(), mine)

    def test_a_linked_herdr_config_is_not_replaced(self):
        os.makedirs(os.path.dirname(su.HERDR_CONFIG))
        real = os.path.join(self.d, "dotfiles.toml")
        with open(real, "w") as f:
            f.write("[ui]\n")
        os.symlink(real, su.HERDR_CONFIG)
        out, calls = self.herdr_keys()
        self.assertTrue(os.path.islink(su.HERDR_CONFIG))
        self.assertEqual((open(real).read(), calls), ("[ui]\n", []))
        self.assertIn('command = "/bin/claude-focus go"', out)

    def test_herdr_keys_never_take_a_bound_key(self):
        os.makedirs(os.path.dirname(su.HERDR_CONFIG))
        with open(su.HERDR_CONFIG, "w") as f:
            f.write('[keys]\nnew_tab = "prefix+a"\n')
        out, calls = self.herdr_keys()
        self.assertIn("prefix+a already bound", out)
        self.assertEqual(open(su.HERDR_CONFIG).read(), '[keys]\nnew_tab = "prefix+a"\n')

    def test_herdr_integration_is_installed_once(self):
        s = su.Setup(yes=True)
        states = {"integrations": [{"target": "claude", "state": "not_installed"}]}
        with mock.patch.object(su.herdr, "call", return_value=states) as call, \
                contextlib.redirect_stdout(io.StringIO()):
            s.herdr_integration("/x/herdr.sock")
        self.assertEqual(call.call_args_list[-1].args[1:], ("integration.install", {"target": "claude"}))
        states["integrations"][0]["state"] = "current"
        with mock.patch.object(su.herdr, "call", return_value=states) as call, \
                contextlib.redirect_stdout(io.StringIO()):
            s.herdr_integration("/x/herdr.sock")
        self.assertEqual([c.args[1] for c in call.call_args_list], ["integration.list"])


class HerdrKeysTest(unittest.TestCase):
    def test_a_path_with_a_space_is_quoted(self):
        block = su.herdr_keys("/Users/ana/My Tools/claude-focus", "prefix+a", "prefix+f")
        self.assertIn("command = \"'/Users/ana/My Tools/claude-focus' go\"", block)

    def test_add_and_remove_restore_the_text(self):
        block = su.herdr_keys("/bin/claude-focus", "prefix+a", "prefix+f")
        for text in ("", "[ui]\naccent = \"cyan\"\n"):
            added, changed = su.add_herdr_keys(text, block)
            self.assertTrue(changed)
            self.assertEqual(su.add_herdr_keys(added, block), (added, False))
            self.assertEqual(su.remove_herdr_keys(added), (text, True))


if __name__ == "__main__":
    unittest.main()
