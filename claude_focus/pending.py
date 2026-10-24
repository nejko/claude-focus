"""Pending waits. A session that waits on something external runs
`claude-focus pending --until ... | --check ...`. The focus app hides it from
the queue, and when the time comes or the check succeeds, it types a wake-up
message into the session, so Claude does the follow-up before the user sees it."""
import datetime as dt
import fcntl
import json
import os
import re
import shlex
import subprocess
import time

from . import store

SESSIONS_DIR = os.path.expanduser("~/.claude/sessions")
ENGINE_LOCK = os.path.join(store.DIR, "engine.lock")


def own_session():
    """The Claude Code session that runs this process: walk up the process
    tree to the first pid that has ~/.claude/sessions/<pid>.json."""
    pid = os.getppid()
    for _ in range(15):
        path = os.path.join(SESSIONS_DIR, f"{pid}.json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except (OSError, ValueError):
                return None
        try:
            pid = int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                     capture_output=True, text=True).stdout.strip() or 0)
        except (OSError, ValueError):
            return None
        if pid <= 1:
            return None
    return None


def take_engine_lock():
    """Only one process (the background service, or else one focus window)
    may score and run the waker, or every wake-up would be typed twice.
    Returns the open lock file (keep it open), or None when another process
    holds it."""
    store.ensure()
    f = open(ENGINE_LOCK, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


SERVICE_PID = os.path.join(store.DIR, "service.pid")


def service_alive():
    """True while the background service process runs (it may not hold the
    engine lock yet)."""
    try:
        with open(SERVICE_PID) as f:
            os.kill(int(f.read().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def app_running():
    f = take_engine_lock()
    if f is None:
        return True
    f.close()
    return False


# The to-do of a wait set in a turn that answers the user's own message. The
# engine marks it done when their keys are in the session's pane after the turn.
READ_ANSWER = "Read its answer to your message"


def read_answer_reason(state, sess, answer):
    """Why a new wait of this session gets the to-do READ_ANSWER, or None.
    `answer` is sessions.last_user_answer() of the session: "running", or
    the Unix ms when the turn that answered their last message ended."""
    prev = state.get("pending", {}).get(sess["sessionId"]) or {}
    if prev.get("needs_auto") and not prev.get("needs_done") and prev.get("pid") in (None, sess["pid"]):
        return store.say("{User} has not looked at your answer to their message yet")  # the wait it replaces
    if answer == "running":
        return store.say("this turn answers a message from {user}")
    since = state.get("seen_since")  # the engine records "seen" times from then on
    if (isinstance(answer, (int, float)) and since and answer >= since
            and state.get("seen", {}).get(sess["sessionId"], 0) < answer):
        return store.say("{User} has not looked at this session since your answer to their last message")
    return None


def set_pending(sess, until_ms, check, every_min, note, needs="", needs_auto=False):
    record = {
        "set_at": int(time.time() * 1000), "pid": sess["pid"], "cwd": sess.get("cwd"),
        "name": sess.get("name"), "until": until_ms, "check": check,
        "every_min": every_min, "note": note, "needs": needs or None, "needs_done": False,
        "needs_auto": needs_auto,
        "armed_stamp": None, "last_check": None, "last_rc": None, "last_out": "",
    }
    store.update_state(lambda st: st.setdefault("pending", {}).__setitem__(sess["sessionId"], record))
    return record


TRY_TIMEOUT = 30  # seconds for the one run of a check when it is set


def try_check(check, cwd):
    """The exit code of one run of the check now, or None when it takes longer
    than TRY_TIMEOUT. A check that already succeeds would wake the session as
    soon as its turn ends, and again after every turn it sets the wait in:
    linkmcp's counted the calls since a fixed date (33 >= 5) and woke it at
    once after each wake-up (2026-10-23 and 24)."""
    try:
        return subprocess.run(check, shell=True, cwd=cwd or os.path.expanduser("~"), timeout=TRY_TIMEOUT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    except (subprocess.TimeoutExpired, OSError):  # OSError: the folder is gone
        return None


def clear_pending(session_id):
    store.update_state(lambda st: st.setdefault("pending", {}).pop(session_id, None))


WAKE_PREFIX = "[claude-focus wake-up]"


def rearm_command(p):
    """The `claude-focus pending` command that sets this wait again."""
    parts = ["claude-focus pending"]
    if p.get("check"):
        parts += ["--check", shlex.quote(p["check"]), "--every", str(p.get("every_min", 5))]
    if p.get("until"):
        until = dt.datetime.fromtimestamp(p["until"] / 1000).strftime("%Y-%m-%d %H:%M")
        parts += ["--until", shlex.quote(until)]
    if p.get("note"):
        parts += ["--note", shlex.quote(p["note"])]
    return " ".join(parts)


# Prompts that start a turn without the user. The hook gets only the text: no
# origin field (Claude Code 2.1, seen 2026-10-15).
NOT_USER = re.compile(r"\s*(<task-notification>|<cross-session-message|Another Claude session sent a message)")


def on_prompt(hook):
    """UserPromptSubmit hook. The user typed into a session that waits, so
    the wait ends: they took over. Tell Claude, or it reports a wait that no
    longer exists, and the session lands in the user's queue when the turn ends.
    A background task notification or a message from another session keeps
    the wait. Returns the context for Claude, or None."""
    sid = hook.get("session_id")
    prompt = hook.get("prompt") or ""
    if not sid or prompt.lstrip().startswith("[claude-focus "):
        return None  # a wake-up or restore message from claude-focus, not the user
    if sid not in store.load_state().get("pending", {}):
        return None  # the usual case: one read, no write
    own = own_session()
    own_pid = own["pid"] if own and own.get("sessionId") == sid else None
    if NOT_USER.match(prompt):
        p = store.load_state().get("pending", {}).get(sid)
        return kept_message(p) if p and (own_pid is None or p.get("pid") in (None, own_pid)) else None
    taken = []

    def fn(st):
        p = st.get("pending", {}).get(sid)
        if p and (own_pid is None or p.get("pid") in (None, own_pid)):
            taken.append(st["pending"].pop(sid))
    store.update_state(fn)
    if not taken:
        return None  # the wait of a duplicate window of this conversation
    p = taken[0]
    user, User = store.user(), store.user(cap=True)
    store.log(f"pending cleared, {user} typed: {p.get('name')}")
    note = f' ("{p["note"]}")' if p.get("note") else ""
    asked = (f' Your request to {user} in that wait was: "{p["needs"]}".'
             if p.get("needs") and not p.get("needs_done") and not p.get("needs_auto") else "")
    return (f"[claude-focus] {User} typed into this session, so your claude-focus wait{note} "
            f"has ended.{asked} When this turn ends, the session goes back into {user}'s queue "
            "as one that needs them. If at the end of this turn you still wait for that, run "
            f"this again in Bash: {rearm_command(p)}\n"
            f"If {user} must also do something for this session meanwhile (for example review and "
            'send a draft), add --needs "<what they must do>": the session then shows under '
            "YOUR TO-DOS in their queue with that request, and the wait still wakes you. A wait that you plan to set "
            "again later (\"when we agree\", \"after this\") is a wait now: set it now, with an "
            "earlier --until if you must also do a step at a set time. Do not run it only when "
            f"you need {user} and wait for nothing external; then end the turn with one concrete "
            "question to them (AskUserQuestion), so their queue shows what you need. Do not tell "
            f"{user} that the session still waits unless you ran it again.")


def kept_message(p):
    """For a turn that a background task or another session started."""
    note = f' ("{p["note"]}")' if p.get("note") else ""
    when = []
    if p.get("check"):
        when.append("when its check succeeds")
    if p.get("until"):
        when.append(("at the latest at " if p.get("check") else "at ") + fmt_ms(p["until"]))
    user, User = store.user(), store.user(cap=True)
    return (f"[claude-focus] {User} did not type this: a background task or another session started "
            f"this turn. Your claude-focus wait{note} is still set, so when this turn ends, the "
            f"session stays out of {user}'s queue until claude-focus wakes it ({' or '.join(when)}). "
            f"If this event means that you now need {user}, run `claude-focus pending --clear` and end "
            "the turn with one concrete question to them (AskUserQuestion). If only the wait changed, "
            "run claude-focus pending again with the new values.")


def fmt_ms(ms):
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%a %d %b %H:%M")


def wake_message(p, reason, output=""):
    """One line (a newline would submit early)."""
    if reason == "time":
        why = f"The time you set has come ({fmt_ms(p['until'])})"
        if p.get("check"):
            why = (f"Your check did not succeed by {fmt_ms(p['until'])} "
                   f"(last exit code {p.get('last_rc')})")
    else:
        why = f"Your check succeeded: `{p['check']}`"
        # Off by default: the output can carry text from outside (an e-mail),
        # and the session would read it as part of a prompt.
        if output.strip() and store.load_config()["wake_check_output"]:
            why += f" (output: {' '.join(output.split())[:300]})"
    note = f' You set this wait for: "{p["note"]}".' if p.get("note") else ""
    if p.get("needs") and not p.get("needs_done"):
        note += (" {User} has not looked at your last answer yet." if p.get("needs_auto")
                 else f' {{User}} has not marked your request done yet: "{p["needs"]}".')
    return store.say(f"{WAKE_PREFIX} {why}.{note}{WAKE_TAIL}")


WAKE_TAIL = (" Check the current state now and do the next step you planned. Before any "
             "irreversible or outward-facing step (send, deploy, publish, pay, delete), ask "
             "{user}, unless they already approved exactly that step. If you must wait again, run "
             "claude-focus pending again.")


def late_wake_message(woke):
    """For a wake-up typed later than its event. Records from before the
    message was saved get a shorter text: the session knows its own wait."""
    if woke.get("message"):
        return f"{woke['message']} (This wake-up is late: it is from {fmt_ms(woke['at'])}.)"  # said already
    return store.say(f"{WAKE_PREFIX} Your claude-focus wait ended at {fmt_ms(woke['at'])}: "
                     f"{woke.get('reason', 'it ended')}.{WAKE_TAIL}")
