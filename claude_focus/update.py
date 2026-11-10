"""A new release of claude-focus: the engine looks on PyPI once a day
(CHECK_HOURS), the focus screen says when one is out, and U (or
`claude-focus update`) installs it the way this copy was installed, then
runs setup, which restarts the service on the new code. Nothing updates by
itself (the user's choice, 2026-11-10), and a copy that runs from a git
checkout is never offered one: its owner pulls."""
import json
import os
import subprocess
import time
import urllib.request

from . import __version__, store

PYPI = "https://pypi.org/pypi/claude-focus/json"
CHECK_HOURS = 24
# How each kind of install upgrades itself (install_kind), one command after
# the other. Tested from 0.1.4 to 0.1.5 on 2026-11-10: `brew upgrade` alone
# saw no new formula (Homebrew refreshes its taps only now and then), and
# `uv tool upgrade` keeps an install made with a version pin where it is.
COMMANDS = {
    "brew": [["brew", "update"], ["brew", "upgrade", "claude-focus"]],
    "uv": [["uv", "tool", "install", "claude-focus@latest"]],
    "pipx": [["pipx", "upgrade", "claude-focus"]],
}
HERE = os.path.dirname(os.path.abspath(__file__))


def install_kind(here=HERE):
    """"checkout" (a git checkout: never updated), "brew", "uv", "pipx", or
    "pip" (another install, which the user upgrades by hand)."""
    if os.path.exists(os.path.join(os.path.dirname(here), ".git")):
        return "checkout"
    for kind, mark in (("brew", "/Cellar/claude-focus/"), ("uv", "/uv/tools/claude-focus/"),
                       ("pipx", "/pipx/venvs/claude-focus/")):
        if mark in here:
            return kind
    return "pip"


def version_tuple(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except (AttributeError, ValueError):
        return ()


def newer(latest, current=__version__):
    return version_tuple(latest) > version_tuple(current)


def latest(url=PYPI, timeout=10):
    """The newest version on PyPI, or None (offline, or PyPI changed)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)["info"]["version"]
    except (OSError, ValueError, KeyError):
        return None


def due(state, now=None):
    """True when the last look at PyPI is CHECK_HOURS old (or never was)."""
    last = (state.get("update") or {}).get("checked_at") or 0
    return ((now or time.time()) * 1000 - last) > CHECK_HOURS * 3.6e6


def check(kind=None):
    """Look at PyPI and keep the answer in the state (update.latest), for
    the focus screen. Not for a checkout. Returns the newest version or None."""
    if (kind or install_kind()) == "checkout":
        return None
    v = latest()
    if v:
        store.update_state(lambda st: st.__setitem__(
            "update", {"latest": v, "checked_at": int(time.time() * 1000)}))
        if newer(v):
            store.log(f"update: claude-focus {v} is out (this is {__version__})")
    return v


def available(state, kind=None):
    """The newer version the focus screen offers, or None."""
    v = (state.get("update") or {}).get("latest")
    return v if v and newer(v) and (kind or install_kind()) != "checkout" else None


def installed():
    """The version that `claude-focus` on PATH is now, or None."""
    try:
        out = subprocess.run(["claude-focus", "--version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.split()[-1] if out.strip() else None


def run(kind=None, setup=True, say=print):
    """Upgrade with the command of this install, then `claude-focus setup
    --yes`, which restarts the service on the new code. Returns (ok, text)."""
    kind = kind or install_kind()
    cmds = COMMANDS.get(kind)
    if not cmds:
        return False, ("this copy runs from a git checkout: pull it" if kind == "checkout"
                       else "upgrade it the way you installed it, for example pip install -U claude-focus")
    for cmd in cmds:
        say(f"Updating: {' '.join(cmd)}")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as ex:
            return False, f"{' '.join(cmd)} failed: {ex}"
        if r.returncode:
            return False, f"{' '.join(cmd)} failed: {(r.stderr or r.stdout).strip()[-200:]}"
    now = installed()
    if not newer(now or ""):
        # The Homebrew formula follows a PyPI release by hand, so brew can have nothing newer yet.
        return False, f"found nothing newer than {now or __version__} yet; try again later"
    store.log(f"update: {kind} installed {now}")
    if setup:
        say("Running claude-focus setup --yes")
        try:
            s = subprocess.run(["claude-focus", "setup", "--yes"], capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as ex:
            return False, f"setup failed: {ex}"
        if s.returncode:
            return False, f"setup failed: {(s.stderr or s.stdout).strip()[-200:]}"
    return True, "updated: press q and open claude-focus again for the new version"
