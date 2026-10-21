"""Read the live Claude Code sessions and the last thing each one said.

Source of truth is `claude agents --json` (documented, covers interactive and
background sessions). Transcripts give the last assistant text; a question that
is still open is not in the transcript yet, so the scorer also reads the
screen (see iterm.py).
"""
import datetime as dt
import json
import os
import re
import subprocess
import time

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
TAIL_BYTES = 600_000
SHORT_REPLY = 60  # characters; shorter final replies fall back to the last report


SESSIONS_DIR = os.path.join(HOME, ".claude", "sessions")


def _from_files():
    """Read ~/.claude/sessions/<pid>.json directly: the same data as
    `claude agents --json` for a fraction of the CPU (that command costs
    about 0.3 s CPU per call). Returns None if the format looks unfamiliar."""
    try:
        names = [n for n in os.listdir(SESSIONS_DIR) if n.endswith(".json")]
    except OSError:
        return None
    rows = []
    for n in names:
        try:
            with open(os.path.join(SESSIONS_DIR, n)) as f:
                d = json.load(f)
            os.kill(int(d["pid"]), 0)
        except (OSError, ValueError, KeyError, TypeError):
            continue  # stale file of an exited process, or a partial write
        if "status" not in d or "sessionId" not in d:
            return None
        rows.append(d)
    return rows


def _from_cli():
    try:
        out = subprocess.run(
            ["claude", "agents", "--json"], capture_output=True, text=True,
            cwd=HOME, timeout=20,
        ).stdout
        return json.loads(out or "[]")
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def list_sessions(use_cli=False):
    """Live sessions with a process. Each dict has at least sessionId, pid,
    cwd, status, name, statusSince and key; waitingFor when status is
    waiting; dup when several processes run one conversation."""
    rows = None if use_cli else _from_files()
    if rows is None:
        rows = _from_cli()
    if rows is None:
        return None  # unknown; caller keeps the previous list
    live = []
    for r in rows:
        if not r.get("pid") or not r.get("sessionId") or not r.get("status"):
            continue
        if r.get("parkedJobId"):
            continue  # moved to the background (/bg): its background job is the live row
        if (r.get("entrypoint") or "").startswith("sdk-"):
            continue  # a headless run (claude -p, Agent SDK) of a script: no pane, no one to answer

        r["statusSince"] = _status_since(r)
        r["key"] = r["sessionId"]
        live.append(r)
    # Two processes can run the same conversation (a --resume while the old
    # window is still open). Both write to one transcript, which the user must
    # know. The most recently active one keeps the plain key.
    by_id = {}
    for r in live:
        by_id.setdefault(r["sessionId"], []).append(r)
    for group in by_id.values():
        if len(group) > 1:
            group.sort(key=lambda r: -r["statusSince"])
            for i, r in enumerate(group):
                r["dup"] = len(group)
                if i:
                    r["key"] = f"{r['sessionId']}#{r['pid']}"
    return live


def _status_since(row):
    """Unix ms of the last status change. `claude agents --json` does not
    return it, so read the per-pid session file; fall back to start time."""
    if row.get("statusUpdatedAt"):
        return row["statusUpdatedAt"]
    path = os.path.join(SESSIONS_DIR, f"{row['pid']}.json")
    try:
        with open(path) as f:
            data = json.load(f)
        if data.get("sessionId") == row["sessionId"]:
            return data.get("statusUpdatedAt") or row.get("startedAt")
    except (OSError, ValueError):
        pass
    return row.get("startedAt") or int(time.time() * 1000)


def status_of(pid, session_id):
    """(status, statusUpdatedAt) of one running session, read from its pid
    file; None when the process ended or the file holds another session."""
    try:
        with open(os.path.join(SESSIONS_DIR, f"{pid}.json")) as f:
            d = json.load(f)
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return None
    if d.get("sessionId") != session_id:
        return None
    return d.get("status"), d.get("statusUpdatedAt")


def last_question(cwd, session_id):
    """(id, outcome) of the last AskUserQuestion call in the transcript, or
    (None, None). Claude Code writes the call only when its dialog closes,
    together with the result: outcome is "answered" when the user submitted the
    answers, "declined" for Esc or "Chat about this" (both give an error
    result), None when the result is not written yet."""
    path = transcript_path(cwd, session_id)
    if not path:
        return None, None
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return None, None
    found, outcome = None, None
    for line in raw.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        content = e.get("message", {}).get("content")
        if e.get("isSidechain") or not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if e.get("type") == "assistant" and c.get("type") == "tool_use" and c.get("name") == "AskUserQuestion":
                found, outcome = c.get("id"), None
            elif e.get("type") == "user" and c.get("type") == "tool_result" and found and c.get("tool_use_id") == found:
                outcome = "declined" if c.get("is_error") else "answered"
    return found, outcome


CLAUDE_FOCUS_TEXT = re.compile(r"\s*(<pasted_content[^>]*>\s*)?\[claude-focus ")


def _typed_by_user(e):
    """A prompt the user typed or queued. claude-focus types its wake-up and
    restore messages into the prompt too (a long one arrives as pasted
    content), so those are excluded by their text. The summary that a
    compaction writes is a user entry without an origin field."""
    if e.get("type") != "user" or e.get("isSidechain") or e.get("isMeta") or e.get("isCompactSummary"):
        return False
    content = e.get("message", {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return False
        text = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    else:
        return False
    if CLAUDE_FOCUS_TEXT.match(text):
        return False
    origin = e.get("origin")
    if isinstance(origin, dict):  # "human"; "task-notification" and "peer" are not the user
        return origin.get("kind") == "human"
    return not text.lstrip().startswith(("<", "[Request interrupted"))  # no origin field


TURN_END = b"turn_duration"  # a quick test before the JSON parse of a line
CLOCK_SLACK_MS = 60_000  # transcript entries are not always in strict time order
LOOK_BACK = 16 << 20  # bytes: how far back last_user_answer looks for their message


def _is_turn_end(line):
    try:
        e = json.loads(line)
    except ValueError:
        return False
    return e.get("type") == "system" and e.get("subtype") == "turn_duration" and not e.get("isSidechain")


def _ms(e):
    """Unix ms of a transcript entry's timestamp ("2026-10-14T19:07:13.064Z"), or None."""
    try:
        return dt.datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp() * 1000
    except (KeyError, AttributeError, ValueError):
        return None


def _lines_back(path, chunk=1 << 20):
    """The lines (bytes) of a transcript, from the last to the first. A
    prompt with screenshots is one line of several MB, so the file is read
    back in chunks, and a line is never cut."""
    with open(path, "rb") as f:
        pos, carry = f.seek(0, os.SEEK_END), b""
        while pos > 0:
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            lines = (f.read(step) + carry).split(b"\n")
            carry, lines = (lines[0], lines[1:]) if pos > 0 else (b"", lines)  # lines[0] can be partial
            yield from reversed(lines)


def current_turn(path, chunk=1 << 20):
    """The transcript lines (bytes) after the last turn end, which Claude
    Code marks with a turn_duration entry."""
    after = []
    for line in _lines_back(path, chunk):
        if TURN_END in line and _is_turn_end(line):
            break
        after.append(line)
    return after[::-1]


def last_user_answer(cwd, session_id):
    """Where the answer to the user's last message is. "running": the running
    turn holds their message. A number: the Unix ms when the turn that answered
    it ended; the turns after it came from background tasks, other sessions
    or claude-focus. None: no message of theirs in the last LOOK_BACK bytes."""
    path = transcript_path(cwd, session_id)
    if not path:
        return None
    end, read = None, 0
    try:
        for line in _lines_back(path):
            read += len(line) + 1
            if read > LOOK_BACK:
                return None
            if TURN_END in line and _is_turn_end(line):
                end = _ms(json.loads(line)) or 0  # the turn end nearest after their message
            elif b'"user"' in line:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if _typed_by_user(e):
                    return "running" if end is None else end
    except OSError:
        return None
    return None


def turn_has_user_message(cwd, session_id):
    """True when the running turn holds a prompt that the user typed or queued."""
    return last_user_answer(cwd, session_id) == "running"


def user_typed_since(cwd, session_id, since_ms):
    """True when the transcript has a prompt that the user typed after since_ms.
    An idle compaction, the answer to a question dialog, a background task
    notification or another session's message makes a session busy too."""
    path = transcript_path(cwd, session_id)
    if not path:
        return False
    try:
        for line in _lines_back(path):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = _ms(e)
            if t is None:
                continue
            if t < since_ms - CLOCK_SLACK_MS:
                return False
            if t > since_ms and _typed_by_user(e):
                return True
    except OSError:
        return False
    return False


def transcript_path(cwd, session_id):
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    path = os.path.join(PROJECTS, slug, f"{session_id}.jsonl")
    if os.path.exists(path):
        return path
    for d in os.listdir(PROJECTS):
        p = os.path.join(PROJECTS, d, f"{session_id}.jsonl")
        if os.path.exists(p):
            return p
    return None


def last_assistant_text(cwd, session_id, max_chars=3500):
    """The last assistant text of the session, plus the open AskUserQuestion
    questions if the last assistant turn asked some (answered ones are
    dropped when a later user message follows)."""
    path = transcript_path(cwd, session_id)
    if not path:
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    text, substantial, asks, ask_id = "", "", [], None
    for line in raw.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("isSidechain"):
            continue
        if e.get("type") == "user" and not e.get("isMeta"):
            content = e.get("message", {}).get("content")
            blocks = [c for c in content or [] if isinstance(c, dict)] if not isinstance(content, str) else []
            # A typed message, or the answer to the question dialog (a
            # tool_result for that AskUserQuestion call), closes the questions.
            if isinstance(content, str) or any(c.get("type") == "text" for c in blocks) or any(
                    c.get("type") == "tool_result" and c.get("tool_use_id") == ask_id for c in blocks):
                asks, ask_id = [], None
        if e.get("type") != "assistant":
            continue
        for c in e.get("message", {}).get("content") or []:
            if c.get("type") == "text" and c.get("text", "").strip():
                text = c["text"]
                if len(text.strip()) >= SHORT_REPLY:
                    substantial = text
            elif c.get("type") == "tool_use" and c.get("name") == "AskUserQuestion":
                ask_id = c.get("id")
                asks = [
                    q.get("question", "") + " [" +
                    " / ".join(o.get("label", "") for o in q.get("options", [])) + "]"
                    for q in (c.get("input") or {}).get("questions", [])
                ]
    text = text.strip()
    if len(text) < SHORT_REPLY and substantial and substantial.strip() != text:
        # A one-liner such as "No response requested." carries no request;
        # the last real report still says what the session needs.
        text = substantial.strip() + f"\n\n(Latest reply: {text})"
    if len(text) > max_chars:
        text = "..." + text[-max_chars:]
    if asks:
        text += "\n\nOPEN QUESTIONS:\n" + "\n".join("- " + a for a in asks)
    return text
