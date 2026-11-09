"""The Windows layer (system.py, the herdr named pipe, the Run-key service,
the shells of a Windows pane). Most tests run on every system with Windows
switched on by a patch; the ones that need a real named pipe, psutil or the
console run on Windows only."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402
from claude_focus import herdr, system, terminal  # noqa: E402

WINDOWS = sys.platform == "win32"


class PidTest(unittest.TestCase):
    def test_this_process_runs_and_an_ended_one_does_not(self):
        self.assertTrue(system.pid_alive(os.getpid()))
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.assertFalse(system.pid_alive(proc.pid))
        for bad in (None, "", "x", 0, -1):
            self.assertFalse(system.pid_alive(bad))

    def test_asking_never_stops_the_process(self):
        """On Windows os.kill(pid, 0) sends Ctrl+C (signal 0 is CTRL_C_EVENT)."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(proc.kill)
        for _ in range(5):
            self.assertTrue(system.pid_alive(proc.pid))
        time.sleep(0.3)
        self.assertIsNone(proc.poll())


class LockTest(unittest.TestCase):
    def test_a_second_holder_waits(self):
        path = os.path.join(tempfile.mkdtemp(), "x.lock")
        a, b = open(path, "w"), open(path, "w")
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        self.assertTrue(system.lock(a, block=False))
        self.assertFalse(system.lock(b, block=False))
        got = []
        t = threading.Thread(target=lambda: got.append(system.lock(b)))
        t.start()
        time.sleep(0.2)
        self.assertEqual(got, [])  # still waiting
        system.unlock(a)
        t.join(5)
        self.assertEqual(got, [True])


class ReplaceTest(unittest.TestCase):
    def test_a_reader_with_the_file_open_only_delays_it(self):
        d = tempfile.mkdtemp()
        dst, src = os.path.join(d, "state.json"), os.path.join(d, "state.json.tmp")
        with open(dst, "w") as f:
            f.write("old")
        with open(src, "w") as f:
            f.write("new")
        reader = open(dst)
        threading.Timer(0.2, reader.close).start()
        system.replace(src, dst)
        with open(dst) as f:
            self.assertEqual(f.read(), "new")


class ShellTest(unittest.TestCase):
    def test_shell_names(self):
        for job, name in (("-zsh", "zsh"), ("zsh", "zsh"), ("powershell.exe", "powershell"),
                          (r"C:\Program Files\PowerShell\7\pwsh.exe", "pwsh"), ("CMD.EXE", "cmd"),
                          ("bash.exe", "bash"), ("claude.exe", None), ("2.1.319", None), ("", None), (None, None)):
            self.assertEqual(terminal.shell_name(job), name, job)

    def test_a_new_session_in_each_shell(self):
        """Windows PowerShell 5.1 has no `&&`; cmd.exe needs cd /d; Esc
        clears a typed line in both."""
        path = r"C:\Users\ana\My 'quoted' app"
        self.assertEqual(terminal.start_command("powershell.exe", path, "claude --continue"),
                         "\x1bSet-Location -LiteralPath 'C:\\Users\\ana\\My ''quoted'' app'; "
                         "if ($?) { claude --continue }\r")
        self.assertEqual(terminal.start_command("pwsh", r"C:\x", "claude", create=True),
                         "\x1bNew-Item -ItemType Directory -Force -Path 'C:\\x' | Out-Null; "
                         "Set-Location -LiteralPath 'C:\\x'; if ($?) { claude }\r")
        self.assertEqual(terminal.start_command("cmd.exe", r"C:\x y", "claude", create=True),
                         '\x1b(if not exist "C:\\x y" mkdir "C:\\x y") && cd /d "C:\\x y" && claude\r')
        self.assertEqual(terminal.start_command("-zsh", "/Users/ana/x y", "claude"),
                         "\x15cd '/Users/ana/x y' && claude\r")
        self.assertIsNone(terminal.start_command("vim", "/x", "claude"))


class KeysTest(unittest.TestCase):
    def read(self, keys):
        it = iter(keys)
        left = [len(keys)]

        def getwch():
            left[0] -= 1
            return next(it)
        return appmod.win_keys(getwch, lambda: left[0] > 0)

    def test_arrows_become_escape_sequences(self):
        self.assertEqual(self.read(["\xe0", "H"]), "\x1b[A")
        self.assertEqual(self.read(["\x00", "P"]), "\x1b[B")
        self.assertEqual(appmod.tokens(self.read(["\xe0", "H", "\xe0", "P", "j", "\r"])),
                         ["\x1b[A", "\x1b[B", "j", "\r"])
        self.assertEqual(self.read(["\x1b"]), "\x1b")
        self.assertEqual(appmod.tokens(self.read(["\x1b", "[", "A"])), ["\x1b[A"])  # VT input passes through

    def test_ctrl_c_ends_the_screen(self):
        with self.assertRaises(KeyboardInterrupt):
            self.read(["\x03"])


class PathsTest(unittest.TestCase):
    def test_herdr_config_folder(self):
        env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        with mock.patch.dict(os.environ, dict(env, APPDATA=r"C:\Users\ana\AppData\Roaming"), clear=True), \
                mock.patch.object(system, "WINDOWS", True):
            self.assertEqual(herdr.config_dir(), os.path.join(r"C:\Users\ana\AppData\Roaming", "herdr"))
        with mock.patch.dict(os.environ, dict(env, XDG_CONFIG_HOME="/cfg")):
            self.assertEqual(herdr.config_dir(), os.path.join("/cfg", "herdr"))

    def test_pipe_name(self):
        self.assertEqual(herdr.pipe_name(r"C:\Users\ana\AppData\Roaming\herdr\herdr.sock"),
                         r"\\.\pipe\C:\Users\ana\AppData\Roaming\herdr\herdr.sock")
        self.assertEqual(herdr.pipe_name(r"\\.\pipe\x"), r"\\.\pipe\x")

    def test_hook_path_survives_git_bash(self):
        with mock.patch.object(system, "WINDOWS", True):
            self.assertEqual(system.hook_path(r"C:\Users\ana\.local\bin\claude-focus.exe"),
                             '"C:/Users/ana/.local/bin/claude-focus.exe"')
        with mock.patch.object(system, "WINDOWS", False):
            self.assertEqual(system.hook_path("/opt/homebrew/bin/claude-focus"), "/opt/homebrew/bin/claude-focus")

    def test_the_hook_is_found_in_either_form(self):
        from claude_focus import setup as su
        for command in ("/opt/homebrew/bin/claude-focus hook prompt-submit",
                        '"C:/Users/ana/.local/bin/claude-focus.exe" hook prompt-submit'):
            settings = {}
            self.assertFalse(su.add_hook({"hooks": {"UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": command}]}]}}, "x"))
            self.assertTrue(su.add_hook(settings, command))
            self.assertTrue(su.remove_hook(settings))
            self.assertEqual(settings, {})


class FrontTest(unittest.TestCase):
    """The front window is herdr's when its process is an ancestor of a
    herdr client (measured on Windows 11, 2026-11-09)."""
    PROCS = {7804: (900, "WindowsTerminal.exe", []), 8812: (7804, "herdr.exe", ["herdr.exe"]),
             3900: (8812, "herdr.exe", ["herdr.exe", "server"]), 5000: (900, "chrome.exe", [])}

    def test_front(self):
        f = herdr._front_is_herdr_windows
        self.assertTrue(f(7804, self.PROCS))
        self.assertFalse(f(5000, self.PROCS))
        with mock.patch.object(system, "front_pid", return_value=None):  # no desktop (an SSH login)
            self.assertIsNone(f(None, self.PROCS))
        no_client = {k: v for k, v in self.PROCS.items() if k != 8812}
        self.assertFalse(f(7804, {**no_client, 3900: (1, "herdr.exe", ["herdr.exe", "server"])}))
        adopted = {**self.PROCS, 8812: (600, "herdr.exe", ["herdr.exe"]), 600: (1, "explorer.exe", [])}
        self.assertIsNone(f(7804, adopted))  # a shell that Windows Terminal adopted: unknown

    def test_where_sessions_run(self):
        procs = {100: (3900, "claude.exe", []), 3900: (8812, "herdr.exe", []), 8812: (1, "herdr.exe", []),
                 200: (1, "claude.exe", [])}
        with mock.patch.object(system, "WINDOWS", True), mock.patch.object(system, "processes", return_value=procs):
            self.assertEqual(terminal.where_sessions_run([100, 200]), {terminal.HERDR: 1, terminal.ITERM: 0})


class ShellArgvTest(unittest.TestCase):
    def test_checks_run_in_git_bash_on_windows(self):
        with mock.patch.object(system, "WINDOWS", True), \
                mock.patch.object(system, "git_bash", return_value=r"C:\Program Files\Git\bin\bash.exe"):
            self.assertEqual(system.shell_argv("test -f x"), [r"C:\Program Files\Git\bin\bash.exe", "-c", "test -f x"])
        with mock.patch.object(system, "WINDOWS", True), mock.patch.object(system, "git_bash", return_value=None), \
                mock.patch.dict(os.environ, {"ComSpec": r"C:\Windows\system32\cmd.exe"}):
            self.assertEqual(system.shell_argv("x"), [r"C:\Windows\system32\cmd.exe", "/d", "/c", "x"])
        with mock.patch.object(system, "WINDOWS", False):
            self.assertEqual(system.shell_argv("x"), ["/bin/sh", "-c", "x"])

    @unittest.skipUnless(WINDOWS, "Git Bash: Windows only")
    def test_git_bash_runs_a_check(self):
        bash = system.git_bash()
        self.assertTrue(bash and bash.lower().endswith("bash.exe") and "system32" not in bash.lower(), bash)
        out = subprocess.run(system.shell_argv('test -d "$HOME" && grep -c x <<< "x" && echo ok'),
                             capture_output=True, text=True, timeout=30, **system.HIDDEN)
        self.assertEqual((out.returncode, out.stdout.split()), (0, ["1", "ok"]), out.stderr)


@unittest.skipUnless(WINDOWS, "named pipes: Windows only")
class PipeTest(unittest.TestCase):
    """herdr's named pipe: one request per connection, an answer within the
    timeout, and the event stream."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "h.sock")
        self.calls = []

    def serve(self, fn, reply=True):
        async def handle(reader, writer):
            line = await reader.readline()
            req = json.loads(line)
            self.calls.append(req["method"])
            if req["method"] == "events.subscribe":
                writer.write(b'{"id":"x","result":{"type":"subscribed"}}\n')
                for i in range(3):
                    writer.write((json.dumps({"event": "pane.focused", "n": i}) + "\n").encode())
                await writer.drain()
                await asyncio.sleep(1)
            elif reply:
                writer.write((json.dumps({"id": req["id"], "result": {"type": "pong", "protocol": 22}}) + "\n")
                             .encode())
                await writer.drain()
            else:
                await asyncio.sleep(3)  # never answers
            writer.close()

        async def main():
            loop = asyncio.get_running_loop()
            servers = await loop.start_serving_pipe(
                lambda: asyncio.StreamReaderProtocol(asyncio.StreamReader(), handle), herdr.pipe_name(self.path))
            try:
                return await fn()
            finally:
                for s in servers:
                    s.close()
        return asyncio.run(main())

    def test_requests_one_after_another_and_side_by_side(self):
        async def fn():
            loop = asyncio.get_running_loop()
            first = await loop.run_in_executor(None, herdr.ping, self.path)
            many = await asyncio.gather(*[herdr.request(self.path, "ping") for _ in range(8)])
            return first, many
        first, many = self.serve(fn)
        self.assertEqual(first["protocol"], 22)
        self.assertEqual([m["type"] for m in many], ["pong"] * 8)
        self.assertEqual(len(self.calls), 9)

    def test_no_server_and_a_silent_server(self):
        self.assertIsNone(herdr.ping(os.path.join(tempfile.mkdtemp(), "none.sock")))

        async def fn():
            t = time.monotonic()
            with self.assertRaises(herdr.Error):
                await herdr.request(self.path, "ping", timeout=0.5)
            return time.monotonic() - t
        self.assertLess(self.serve(fn, reply=False), 2)

    def test_events(self):
        async def fn():
            b = herdr.Bridge(self.path)
            out = []
            async for ev in b._events(["pane.focused"]):
                out.append(ev["n"])
                if len(out) == 3:
                    break
            return out
        self.assertEqual(self.serve(fn), [0, 1, 2])


@unittest.skipUnless(WINDOWS, "the Run key: Windows only")
class ServiceTest(unittest.TestCase):
    def test_the_command_runs_this_checkout(self):
        from claude_focus import winservice
        argv = winservice.argv("daemon")
        self.assertTrue(argv[0].lower().endswith(("pythonw.exe", "python.exe")))
        self.assertEqual(argv[-1], "daemon")
        out = subprocess.run([sys.executable, *argv[1:-1], "--version"], capture_output=True, text=True,
                             timeout=60)
        self.assertIn("claude-focus", out.stdout)


if __name__ == "__main__":
    unittest.main()
