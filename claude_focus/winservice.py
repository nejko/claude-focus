r"""The background service on Windows, where there is no launchd. A value in
the user's Run key (HKCU\Software\Microsoft\Windows\CurrentVersion\Run: no
admin rights) starts a supervisor at each logon, without a console window
(pythonw.exe). The supervisor runs the engine (`claude-focus daemon --run`)
as its child and starts it again THROTTLE seconds after it exits, as
launchd's KeepAlive and ThrottleInterval do on macOS: the engine exits when
herdr quits or the sessions move, and waits for a terminal when it starts."""
import os
import subprocess
import sys
import time

from . import pending, store, system

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "claude-focus"
LABEL = f"HKCU\\{RUN_KEY}\\{VALUE}"
SUPERVISOR_PID = os.path.join(store.DIR, "supervisor.pid")
SUPERVISOR_LOCK = os.path.join(store.DIR, "supervisor.lock")
LOG = os.path.join(store.DIR, "service.log")
THROTTLE = 30             # seconds between two starts of the engine
STOP_WAIT = 5             # seconds a stopped process gets before it is killed


def pythonw():
    """pythonw.exe next to this Python (no console window), else this Python."""
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return exe if os.path.isfile(exe) else sys.executable


def argv(*args):
    """The command that runs `claude-focus <args>` with pythonw. The folder
    that holds the package goes first on sys.path, so the service also runs
    from a checkout that is not installed (macOS sets PYTHONPATH instead)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    boot = f"import sys; sys.path.insert(0, {root!r}); from claude_focus.cli import main; main()"
    return [pythonw(), "-c", boot, *args]


def registered():
    """The command line in the Run key, or None."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, VALUE)[0]
    except OSError:
        return None


def install():
    """Register the supervisor for each logon, and (re)start it now on this code."""
    import winreg
    store.ensure()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, subprocess.list2cmdline(argv("daemon")))
    stop()
    start()


def uninstall():
    import winreg
    stop()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE)
    except OSError:
        pass


def start():
    """Start the supervisor detached: it outlives the shell (and herdr pane)
    that ran setup. Outside a job that kills its processes when it closes,
    where Windows allows that."""
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    for extra in (subprocess.CREATE_BREAKAWAY_FROM_JOB, 0):
        try:
            subprocess.Popen(argv("daemon"), creationflags=flags | extra, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             cwd=store.DIR)
            return
        except OSError:
            if not extra:
                raise  # breakaway refused: tried without it


def _pid(path):
    try:
        with open(path, encoding="utf-8") as f:
            pid = int(f.read().strip())
        return pid if system.pid_alive(pid) else None
    except (OSError, ValueError):
        return None


def supervisor_pid():
    return _pid(SUPERVISOR_PID)


def stop():
    """Stop the supervisor and the engine (also an engine whose supervisor died)."""
    import psutil
    procs = []
    for pid in (supervisor_pid(), _pid(pending.SERVICE_PID)):
        if not pid:
            continue
        try:
            p = psutil.Process(pid)
            procs += [p, *p.children(recursive=True)]
        except psutil.Error:
            continue
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=STOP_WAIT)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass


def supervise():
    """The supervisor (the Run key starts it): one per user, it keeps the
    engine running."""
    store.ensure()
    lock = open(SUPERVISOR_LOCK, "w")
    if not system.lock(lock, block=False):
        return  # another supervisor runs
    with open(SUPERVISOR_PID, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    store.log(f"service supervisor start (pid {os.getpid()})")
    while True:
        started = time.time()
        with open(LOG, "a", encoding="utf-8") as log:
            try:
                rc = subprocess.call(argv("daemon", "--run"), stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, cwd=store.DIR, **system.HIDDEN)
            except OSError as ex:
                rc = repr(ex)
        wait = max(0, THROTTLE - (time.time() - started))
        store.log(f"service exited ({rc}); the supervisor starts it again in {wait:.0f} s")
        time.sleep(wait)


def status():
    """Lines for `claude-focus status`."""
    command = registered()
    if not command:
        return ["Service not installed."]
    sup = supervisor_pid()
    return [f"registered: {LABEL}",
            f"supervisor: {'running, pid ' + str(sup) if sup else 'not running'}",
            f"engine: {'running' if pending.service_alive() else 'not running'}"]
