"""Claude Code reads ~/.claude/CLAUDE.md once, when a session starts, and
never again while it runs. When the file changes, the prompt hook tells each
session that started before the change, once, with its next prompt, to read
it again (config "claude_md_notice"). Only with a prompt: a message to every
open session at once would start a turn in each, and its answer ("I read it
again") would take the place of the request it left for the user."""
import datetime as dt
import os

from . import pending as pend, store

PATH = os.path.expanduser("~/.claude/CLAUDE.md")


def notice(hook, path=None):
    """The note for this prompt's session, or None. state "claude_md": session
    id -> the file's mtime (ms) it was checked against; only the current
    version's entries are kept."""
    path = path or PATH
    sid = hook.get("session_id")
    if not sid or not store.load_config()["claude_md_notice"]:
        return None
    try:
        mtime = int(os.stat(path).st_mtime * 1000)
    except OSError:
        return None
    if store.load_state().get("claude_md", {}).get(sid, 0) >= mtime:
        return None  # the usual case: one stat, one read
    own = pend.own_session()
    started = own.get("startedAt") if own and own.get("sessionId") == sid else None

    def fn(st):
        checked = {k: v for k, v in st.get("claude_md", {}).items() if v >= mtime}
        checked[sid] = mtime
        st["claude_md"] = checked
    store.update_state(fn)
    if started and started > mtime:
        return None  # it started after the change and read the new file
    when = dt.datetime.fromtimestamp(mtime / 1000).strftime("%Y-%m-%d %H:%M")
    store.log(f"claude_md notice: {own.get('name') if own else sid}")
    return (f"[claude-focus] {short(path)} changed at {when}, after this session started, so the copy of "
            "it in your context is out of date. Read the file again now and follow its current text.")


def short(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path
