"""What differs between macOS (or another Unix) and Windows: whether a process
runs, file locks, replacing a file that someone reads, subprocesses without a
console window, the process tree, the front window, dark mode, the shell for
check commands. Everything else in claude-focus calls these, so the rest of
the code does not ask which system it runs on.

On Windows, os.kill(pid, 0) does not test a process: signal 0 is
CTRL_C_EVENT, so it would send Ctrl+C to it (or kill it). Use pid_alive."""
import os
import shutil
import subprocess
import sys
import time

WINDOWS = sys.platform == "win32"

# A service without a console (pythonw.exe) would open a console window for
# every console program it starts: claude, bash, git. CREATE_NO_WINDOW stops that.
HIDDEN = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}
REPLACE_TRIES = 50        # x 20 ms: how long a replace waits for a reader to close the file (Windows)


def pid_alive(pid):
    """True while the process runs."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if WINDOWS:
        import psutil
        return psutil.pid_exists(pid)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def lock(f, block=True):
    """Lock the open file f for this process. Returns True; with block=False,
    False when another process holds it. The lock goes when the process ends."""
    if WINDOWS:
        import msvcrt
        while True:
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if not block:
                    return False
                time.sleep(0.05)
    import fcntl
    try:
        fcntl.flock(f, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        return True
    except OSError:
        if block:
            raise
        return False


def unlock(f):
    if WINDOWS:
        import msvcrt
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    import fcntl
    fcntl.flock(f, fcntl.LOCK_UN)


def replace(src, dst):
    """os.replace. On Windows it fails while another process has dst open
    (the focus window reads state.json every poll): try again for up to a second."""
    for i in range(REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not WINDOWS or i == REPLACE_TRIES - 1:
                raise
            time.sleep(0.02)


def utf8_stdio():
    """On Windows, a pipe or file gets the ANSI code page (cp1252), so "…" or
    "❯" in printed text raises an error, and a hook reads Claude Code's UTF-8
    JSON wrongly. Switch stdin, stdout and stderr to UTF-8."""
    if not WINDOWS:
        return
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # None under pythonw.exe, or not a text stream


def processes():
    """{pid: (ppid, name, argv)} of every process; name is the program's file
    name ("herdr.exe"). Windows only (psutil); Unix code reads ps."""
    import psutil
    out = {}
    for p in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
        try:
            out[p.info["pid"]] = (p.info["ppid"] or 0, p.info["name"] or "", p.info["cmdline"] or [])
        except (psutil.Error, KeyError):
            continue
    return out


def children(pid):
    """[{"pid", "name"}] of the direct child processes of pid (Windows: psutil)."""
    import psutil
    try:
        return [{"pid": c.pid, "name": c.name()} for c in psutil.Process(int(pid)).children()]
    except (psutil.Error, ValueError, TypeError):
        return []


def parent_pid(pid):
    """The parent process of pid, or 0."""
    if WINDOWS:
        import psutil
        try:
            return psutil.Process(int(pid)).ppid()
        except (psutil.Error, ValueError):
            return 0
    try:
        return int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                  capture_output=True, text=True).stdout.strip() or 0)
    except (OSError, ValueError):
        return 0


def front_pid():
    """The process of the window that gets the keys (Windows), or None when
    Windows does not say: a service outside the user's desktop session (an
    SSH login) sees no foreground window."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def windows_appearance():
    """Windows' app mode: "dark" or "light" (Settings > Personalization > Colors)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except OSError:
        return "dark"
    return "light" if light else "dark"


def git_bash():
    """Git for Windows' bash.exe, or None. Claude Code runs its Bash tool in
    Git Bash, so a check command that Claude wrote is bash, not cmd.exe.
    Never WSL's System32\\bash.exe."""
    candidates = [os.environ.get("CLAUDE_CODE_GIT_BASH_PATH", "")]
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if root:
            candidates.append(os.path.join(root, "Git", "bin", "bash.exe"))
    git = shutil.which("git")
    if git:  # <Git>\cmd\git.exe -> <Git>\bin\bash.exe
        candidates.append(os.path.join(os.path.dirname(os.path.dirname(git)), "bin", "bash.exe"))
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def shell_argv(command):
    """The argv that runs a shell command line: on Windows Git Bash (cmd.exe
    when there is none), else /bin/sh."""
    if WINDOWS:
        bash = git_bash()
        return [bash, "-c", command] if bash else [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", command]
    return ["/bin/sh", "-c", command]


def program(name):
    """The full path of a program on PATH (Windows finds claude.cmd, an npm
    shim, only by its full name), else the name."""
    return shutil.which(name) or name


def hook_path(path):
    """A program path for a hook command in ~/.claude/settings.json. Claude
    Code runs hooks through Git Bash on Windows, which eats the backslashes of
    C:\\Users\\..., and the hook then never runs: forward slashes, quoted."""
    if not WINDOWS:
        return path
    return '"' + path.replace("\\", "/") + '"'
