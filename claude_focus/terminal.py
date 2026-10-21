"""The terminal that the Claude sessions run in: iTerm2 (iterm.py) or herdr
(herdr.py). Both bridges have the same methods, so the focus screen, the
waker and the service work the same in either. detect() picks one."""
import asyncio
import os
import subprocess
import sys

from . import store

ITERM, HERDR = "iterm", "herdr"
SHELLS = {"zsh", "bash", "fish", "sh", "tcsh", "ksh", "nu"}
NOT_RUNNING = ("claude-focus: neither herdr nor iTerm2 runs. Start the one your Claude sessions run in "
               "(or set \"terminal\" in ~/.claude-focus/config.json).")


def iterm_running():
    """From ps (pgrep sees no processes in some sandboxes)."""
    out = subprocess.run(["ps", "-axo", "comm"], capture_output=True, text=True).stdout
    return any(line.rstrip().endswith("/MacOS/iTerm2") for line in out.splitlines())


def where_sessions_run(pids, ps_out=None):
    """{"herdr": n, "iterm": m}: how many of these processes run in a herdr
    pane and how many in an iTerm2 pane, from their parent processes (a pane's
    shell is a child of the herdr server, or of iTerm2). Needs no API and no
    permission."""
    if ps_out is None:
        ps_out = subprocess.run(["ps", "-axo", "pid=,ppid=,comm="], capture_output=True, text=True).stdout
    parent, comm = {}, {}
    for line in ps_out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            parent[int(parts[0])], comm[int(parts[0])] = int(parts[1]), parts[2].strip()
    counts = {HERDR: 0, ITERM: 0}
    for pid in pids:
        for _ in range(64):  # a process tree is never this deep; a cycle never ends
            name = comm.get(pid, "")
            if os.path.basename(name) == "herdr":
                counts[HERDR] += 1
                break
            if "iTerm" in name:
                counts[ITERM] += 1
                break
            pid = parent.get(pid, 1)
            if pid <= 1:
                break
    return counts


def detect(cfg=None):
    """Which terminal to drive. Config "terminal" when it names one; else the
    one this process runs in (a herdr pane or key binding, an iTerm2 pane);
    else, for the service, the one that runs most of the live Claude
    sessions, by their process tree, when both run; else whichever runs.
    None: neither runs."""
    cfg = cfg or store.load_config()
    choice = cfg.get("terminal") or "auto"
    if choice in (ITERM, HERDR):
        return choice
    # herdr first: its panes can inherit ITERM_SESSION_ID from the terminal that started its server.
    if os.environ.get("HERDR_PANE_ID") or os.environ.get("HERDR_SOCKET_PATH"):
        return HERDR
    if os.environ.get("ITERM_SESSION_ID"):
        return ITERM
    from . import herdr
    herdr_up, iterm_up = herdr.ping(herdr.socket_path(cfg)) is not None, iterm_running()
    if herdr_up and iterm_up:
        from . import sessions as sessions_mod
        n = where_sessions_run([s["pid"] for s in sessions_mod.list_sessions() or []])
        return ITERM if n[ITERM] > n[HERDR] else HERDR
    return HERDR if herdr_up else ITERM if iterm_up else None


def describe(kind, cfg=None):
    """One line for `claude-focus status` and setup."""
    cfg = cfg or store.load_config()
    how = "set in config.json" if cfg.get("terminal") in (ITERM, HERDR) else "detected"
    if kind == HERDR:
        from . import herdr
        path = herdr.socket_path(cfg)
        pong = herdr.ping(path)
        state = f"herdr {pong.get('version')}" if pong else "herdr (not answering)"
        return f"{state} at {path} ({how})"
    if kind == ITERM:
        return f"iTerm2 ({how})"
    return "none: neither herdr nor iTerm2 runs"


def run(fn, kind=None):
    """Run the coroutine function fn(bridge) against the terminal (detect()
    when kind is None) and return its result."""
    kind = kind or detect()
    if kind == HERDR:
        from .herdr import Bridge
        return asyncio.run(fn(Bridge()))
    if kind == ITERM:
        import iterm2

        from .iterm import Bridge
        return iterm2.run_until_complete(lambda conn: fn(Bridge(conn)))
    sys.exit(NOT_RUNNING)
