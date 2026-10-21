"""State and config under ~/.claude-focus/ (atomic JSON writes, flock)."""
import contextlib
import fcntl
import json
import os
import shutil

# CLAUDE_FOCUS_DIR points tests at a scratch folder instead of the real state.
DIR = os.environ.get("CLAUDE_FOCUS_DIR") or os.path.expanduser("~/.claude-focus")
STATE = os.path.join(DIR, "state.json")
CONFIG = os.path.join(DIR, "config.json")
PRIORITIES = os.path.join(DIR, "priorities.md")
LOG = os.path.join(DIR, "focus.log")
LOCK = os.path.join(DIR, ".lock")
TEMPLATE = os.path.join(os.path.dirname(__file__), "priorities.template.md")

DEFAULT_CONFIG = {
    "user_name": "the user",            # how messages to sessions and the scorer name you, e.g. "Ana"
    "user_role": "",                    # optional, for the scorer: "the CEO of two companies"
    "companies": {},                    # folder name prefix -> company label, e.g. {"acme": "Acme"}
    "scorer": "claude",                 # "claude" (your Claude Code login) or "jev" (TypeSafe or OpenRouter key)
    "jev_model": "jev-latest",
    "scorer_model": "claude-sonnet-5-5",  # used only when scorer is "claude"
    "ask_model": "claude-haiku-5-5",    # with jev: writes the one-line request ("" = only ask.extract)
    "scorer_parallel": 4,
    "poll_seconds": 3,
    "aging_points_per_hour": 0.15,
    "aging_max": 10,
    "terminal": "auto",                 # "auto", "herdr" or "iterm": where the sessions run (terminal.detect)
    "herdr_socket": "",                 # herdr's API socket; "" = $HERDR_SOCKET_PATH, else ~/.config/herdr/herdr.sock
    "herdr_sidebar": True,              # put each session's place in the queue into herdr's pane metadata
    "herdr_key_go": "prefix+a",         # the herdr keys that setup adds: go to the top of the queue,
    "herdr_key_focus_window": "prefix+f",  # and show the focus screen
    "hotkeys": False,                   # iTerm2: only in a focus window; not in the service
    "hotkey_go": "ctrl+option+n",
    "hotkey_focus_window": "ctrl+option+f",
    "theme": "default",                 # the focus screen's look: "default" or "norton" (look.THEMES); key t switches
    "zoom": True,
    "return_after_answer": True,        # after Enter on a question: come back when it is answered
    "free_profile": "Available",        # the iTerm2 profile (the green one) of a free terminal
    "session_profile": "Default",       # the profile a free terminal gets when a Claude session starts in it
    "auto_profiles": False,             # switch those two profiles by itself (App._recolor_panes)
    "new_session_command": "claude",    # what Enter in the folder picker types after `cd <folder> && `
    "project_roots": ["~/Projects", "~/projects", "~/code", "~/src", "~/dev", "~/Developer",
                      "~/repos", "~/git", "~/work"],  # their subfolders are offered too (folders.candidates)
    "wake_check_output": False,         # put a check's output into the wake-up message (see pending.wake_message)
}


def ensure():
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    os.chmod(DIR, 0o700)  # state, logs and screen text are for this user only
    if not os.path.exists(PRIORITIES) and os.path.exists(TEMPLATE):
        shutil.copy(TEMPLATE, PRIORITIES)


@contextlib.contextmanager
def locked():
    ensure()
    with open(LOCK, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def load_state():
    st = _read(STATE, {})
    st.setdefault("sessions", {})
    return st


def update_state(fn):
    """Read-modify-write under the lock. fn(state) mutates in place."""
    with locked():
        st = load_state()
        fn(st)
        _write(STATE, st)
        return st


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(_read(CONFIG, {}))
    return cfg


def user(cap=False):
    """The user's name for messages (config "user_name"); cap=True for the
    start of a sentence ("The user")."""
    name = load_config()["user_name"] or "the user"
    return name[:1].upper() + name[1:] if cap else name


def say(text):
    """Text for a session or the scorer, with {user} and {User} replaced by
    the user's name. Plain replace, so other braces in the text stay."""
    return text.replace("{User}", user(cap=True)).replace("{user}", user())


def local_zone():
    """The local time zone's name, such as Europe/Berlin (from
    /etc/localtime), else its abbreviation (CEST)."""
    path = os.path.realpath("/etc/localtime")
    if "zoneinfo/" in path:
        return path.split("zoneinfo/", 1)[1]
    import time
    return time.tzname[time.daylight and time.localtime().tm_isdst > 0]


def priorities_text():
    try:
        with open(PRIORITIES) as f:
            return f.read()
    except OSError:
        return ""


def log(msg):
    import time
    ensure()
    with open(LOG, "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
