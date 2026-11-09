"""A search over past Claude sessions, for the folder picker: a closed
session can be found again by its name, its title or what was said in it,
and resumed in a free terminal (`claude --resume <id>`).

Claude Code's own picker (`claude --resume`, Ctrl+A for every project)
searches names, titles and first prompts, not what was said. Reading every
transcript at each key is too slow (28 GB in 2,079 transcripts on one Mac,
2026-11-09), so `claude-focus index` keeps an SQLite FTS5 index of the
user's messages and Claude's replies (no tool output) in
~/.claude-focus/history.db. The engine runs it every INDEX_SECONDS in its
own process; it reads only what a transcript gained since the last run."""
import calendar
import fcntl
import json
import os
import re
import sqlite3
import time
import unicodedata

from . import store

TRANSCRIPTS = os.path.expanduser("~/.claude/projects")
DB = os.path.join(store.DIR, "history.db")
INDEX_SECONDS = 60
# A longer line is tool output or a copy of a request, never a message to search.
MAX_LINE = 200_000
MAX_TEXT = 20_000  # characters of one message that are indexed
# Sessions a terminal can resume. Headless runs (sdk-*) and the desktop app's
# sessions (claude-desktop, which resume in the app) are left out.
RESUMABLE = (None, "cli")
# User messages that Claude Code writes itself: slash command echoes and output, reminders.
NOT_TYPED = ("<command-", "<local-command-", "<system-reminder>", "<bash-", "<task-notification>",
             "Caveat:", "[Request interrupted")
# The entries worth parsing; the rest (tool output, request copies) are skipped unparsed.
WANTED = re.compile(rb'"type": ?"(?:user|assistant|custom-title|ai-title|last-prompt)"')

# files: one row per transcript. name: the user's (/rename); title: Claude
# Code's; prompt: the last one typed; first: the first message; msgs: how
# many messages are indexed (0: an empty session, opened and closed);
# about, about_n: a one-line description and msgs when it was written.
SCHEMA = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, session TEXT, cwd TEXT, entrypoint TEXT,
    done INTEGER, ino INTEGER, name TEXT, title TEXT, prompt TEXT, first TEXT, msgs INTEGER,
    last_at REAL, about TEXT, about_n INTEGER);
CREATE VIRTUAL TABLE IF NOT EXISTS texts USING fts5(session UNINDEXED, at UNINDEXED, role UNINDEXED, body,
    tokenize='unicode61 remove_diacritics 2');
"""
VERSION = 3  # of SCHEMA: an index of another version is built again


def connect(db=DB):
    con = sqlite3.connect(db, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")  # the focus window reads while the index writes
    if con.execute("PRAGMA user_version").fetchone()[0] != VERSION:
        con.executescript("DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS texts;")
        con.execute(f"PRAGMA user_version={VERSION}")
    con.executescript(SCHEMA)
    return con


def _when(stamp):
    """Unix s of an ISO timestamp such as 2026-10-09T18:28:25.431Z, or None."""
    try:
        return calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


def message_text(entry):
    """The searchable text of a user or assistant entry: what the user typed
    or Claude wrote, without tool calls, tool output, thinking, Claude
    Code's own notes and subagents' messages. "" when there is none."""
    if entry.get("isMeta") or entry.get("isSidechain"):
        return ""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        parts = [c.get("text") or "" for c in content if isinstance(c, dict) and c.get("type") == "text"]
    else:
        return ""
    text = "\n".join(p for p in parts if p and not p.lstrip().startswith(NOT_TYPED)).strip()
    return text[:MAX_TEXT]


def index_file(con, path, session):
    """Add what the transcript gained since the last run. A file that shrank
    or was replaced is indexed again from the start."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    row = con.execute("SELECT done, ino, cwd, entrypoint, name, title, prompt, first, msgs, last_at, about, "
                      "about_n FROM files WHERE path=?", (path,)).fetchone()
    if row and row[1] == st.st_ino and row[0] == st.st_size:
        return False
    if row and row[1] == st.st_ino and row[0] < st.st_size:
        start, (cwd, entry, name, title, prompt, first, msgs, last_at, about, about_n) = row[0], row[2:]
    else:
        con.execute("DELETE FROM texts WHERE session=?", (session,))
        start, cwd, entry, name, title, prompt, first, msgs, last_at, about, about_n = 0, *[None] * 10
    rows, done = [], start
    with open(path, "rb") as f:  # line by line: one transcript had more than 1 GB
        f.seek(start)
        for line in f:
            if not line.endswith(b"\n"):
                break  # a line still being written waits for the next run
            done += len(line)
            if len(line) > MAX_LINE or not WANTED.search(line):
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            kind = e.get("type")
            cwd = e.get("cwd") or cwd
            entry = e.get("entrypoint") or entry
            if kind == "custom-title":
                name = e.get("customTitle") or name
            elif kind == "ai-title":
                title = e.get("aiTitle") or title
            elif kind == "last-prompt":
                prompt = e.get("lastPrompt") or prompt
            elif kind in ("user", "assistant"):
                text = message_text(e)
                at = _when(e.get("timestamp"))
                if text:
                    rows.append((session, at, kind, text))
                    if first is None and kind == "user":
                        first = text[:1000]
                if at:
                    last_at = max(last_at or 0, at)
    con.executemany("INSERT INTO texts (session, at, role, body) VALUES (?, ?, ?, ?)", rows)
    con.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (path, session, cwd, entry, done, st.st_ino, name, title, prompt, first, (msgs or 0) + len(rows),
                 last_at or st.st_mtime, about, about_n))
    con.commit()
    return True


def _alone(db, work):
    """work() unless another run holds the lock (then 0): two runs would add
    the same messages twice, or pay twice for one description."""
    with open(db + ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0
        return work()


def sync(transcripts=TRANSCRIPTS, db=DB):
    """Bring the index up to date: every transcript in transcripts/<folder>/,
    and away with the ones Claude Code deleted. Returns how many changed."""
    return _alone(db, lambda: _sync(transcripts, db))


def run(transcripts=TRANSCRIPTS, db=DB):
    """`claude-focus index`: sync, then describe what needs it (describe)."""
    return _alone(db, lambda: (_sync(transcripts, db), _describe(db))[0])


def _sync(transcripts, db):
    con = connect(db)
    found = {}
    try:
        for d in os.scandir(transcripts):
            if d.is_dir():
                for f in os.scandir(d.path):
                    if f.name.endswith(".jsonl"):
                        found[f.path] = f.name[:-6]
    except OSError:
        pass
    changed = 0
    for path, session in found.items():
        changed += index_file(con, path, session)
    for path, session in con.execute("SELECT path, session FROM files").fetchall():
        if path not in found:
            con.execute("DELETE FROM texts WHERE session=?", (session,))
            con.execute("DELETE FROM files WHERE path=?", (path,))
            changed += 1
    con.commit()
    con.close()
    return changed


def fold(text):
    """Lower case without diacritics: "Šumnik" -> "sumnik"."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def first_line(text, n=120):
    return next((x.strip() for x in (text or "").splitlines() if x.strip()), "")[:n]


def label(name, title, first, about=None):
    """What a picker row says about a session: a one-line description of it
    (about), else Claude Code's title, else its first message; after the
    name the user gave it (/rename), unless that is in the text already. Two
    sessions named after their project ("linkmcp") look the same by name alone."""
    text = about or title or first_line(first)
    if name and fold(name) not in fold(text):
        return f"{name} · {text}" if text else name
    return text or name or "(no title)"


def search(query, db=DB, limit=30):
    """Past sessions that match every word of the query, as dicts with
    session, cwd, label, last (the last prompt), last_at and snippet (the
    matching text, marked with \\x01 and \\x02; None when the name, title,
    description or folder matched). Those whose name, title, description,
    last prompt or folder match come first, then those whose conversation
    does; each group newest first. Empty sessions are left out."""
    words = fold(query).split()
    if not words or not os.path.exists(db):
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        try:
            files = {r[0]: r for r in con.execute(
                "SELECT session, cwd, entrypoint, name, title, prompt, first, last_at, about FROM files "
                "WHERE msgs > 0") if r[2] in RESUMABLE and r[1]}
        except sqlite3.OperationalError:  # an index of an older version, not built again yet
            return []
        head, seen = [], set()
        for s, cwd, _, name, title, prompt, first, last_at, about in files.values():
            text = fold(" ".join(x for x in (name, title, about, prompt, os.path.basename(cwd)) if x))
            if all(w in text for w in words):
                head.append((-(last_at or 0), s, None))
                seen.add(s)
        match = " AND ".join('"' + w.replace('"', '""') + '"*' for w in words)
        body = []
        try:
            for s, at, snip in con.execute(
                    "SELECT session, at, snippet(texts, 3, char(1), char(2), '…', 12) FROM texts "
                    "WHERE texts MATCH ? ORDER BY at DESC LIMIT 2000", (match,)):
                if s in files and s not in seen:
                    seen.add(s)
                    body.append((-(files[s][7] or 0), s, re.sub(r"\s+", " ", snip)))
        except sqlite3.OperationalError:  # a query FTS5 cannot parse
            pass
    finally:
        con.close()
    out = []
    for _, s, snip in sorted(head) + sorted(body):
        _, cwd, _, name, title, prompt, first, last_at, about = files[s]
        out.append({"session": s, "cwd": cwd, "label": label(name, title, first, about), "last": first_line(prompt),
                    "last_at": last_at, "snippet": snip})
    return out[:limit]


# ---- descriptions ---------------------------------------------------------
# Claude Code's title comes from the first prompt, so a session that ran for
# weeks keeps the title of its first task. A small model writes one line on
# what the whole session is about and where it ended. It describes a session
# once its transcript has rested QUIET_S, and again after REDESCRIBE_N more
# messages; up to DESCRIBE_PER_RUN sessions per run, newest first, so the
# first runs work through the backlog a few minutes at a time.
DESCRIBE_PER_RUN = 12
DESCRIBE_PARALLEL = 3
QUIET_S = 600
REDESCRIBE_N = 10
ABOUT_SCHEMA = {"type": "object", "properties": {"about": {"type": "string"}},
                "required": ["about"], "additionalProperties": False}
ABOUT_SYSTEM = """You write a one-line description of a past Claude Code session for {user}'s \
list of sessions, so that {user} recognizes it and knows whether to resume it.

- Say what the session was about, naming the client, project or task, and where it ended \
when that is clear (done, waits for someone, the next step).
- At most 90 characters, in the language of the conversation, no quotes, no final period.
- Use the names and words of the conversation; add nothing it does not say."""


def to_describe(con, now=None, limit=DESCRIBE_PER_RUN):
    """[(path, session, cwd, name, title, first, msgs)] of the sessions that
    need a description now."""
    return con.execute(
        "SELECT path, session, cwd, name, title, first, msgs FROM files WHERE msgs >= 2 "
        "AND (entrypoint IS NULL OR entrypoint = 'cli') AND last_at < ? "
        "AND (about IS NULL OR msgs - about_n >= ?) ORDER BY last_at DESC LIMIT ?",
        ((now or time.time()) - QUIET_S, REDESCRIBE_N, limit)).fetchall()


def about_prompt(con, session, cwd, name, title, first):
    """What the model reads: the names, the first message and the last six messages."""
    last = con.execute("SELECT role, body FROM texts WHERE session=? ORDER BY rowid DESC LIMIT 6",
                       (session,)).fetchall()
    lines = [f"SESSION NAME: {name or '-'}", f"FOLDER: {os.path.basename(cwd or '')}",
             f"TITLE (from the first message): {title or '-'}", "", "=== FIRST MESSAGE ===",
             (first or "")[:1500], "", "=== LAST MESSAGES, oldest first ==="]
    for role, body in reversed(last):
        lines.append(f"{'USER' if role == 'user' else 'CLAUDE'}: {body[:700]}")
    return "\n".join(lines)


def describe(db=DB, model=None, now=None):
    return _alone(db, lambda: _describe(db, model, now))


def _describe(db=DB, model=None, now=None):
    """Write descriptions (to_describe) with the model; stop at the first
    failed call, such as a usage limit, and try again on the next run.
    Returns how many were written."""
    import asyncio

    from . import costs, scorer
    model = model if model is not None else store.load_config().get("history_model")
    if not model:
        return 0
    con = connect(db)
    todo = [(r, about_prompt(con, *r[1:6])) for r in to_describe(con, now)]
    sem = asyncio.Semaphore(DESCRIBE_PARALLEL)
    failed = False

    async def one(row, prompt):
        nonlocal failed
        async with sem:
            if failed:
                return None
            data = await scorer._claude(prompt, store.say(ABOUT_SYSTEM), ABOUT_SCHEMA, model,
                                        f"history {row[1][:8]}")
        if data is None:
            failed = True
            return None
        costs.record(f"history {row[1][:8]}", model, data.get("total_cost_usd"))
        return " ".join(((data.get("structured_output") or {}).get("about") or "").split())[:120]

    async def run():
        return await asyncio.gather(*(one(r, p) for r, p in todo))

    written = 0
    for (row, _), about in zip(todo, asyncio.run(run()) if todo else []):
        if about is not None:  # "" too: no description, and no new try before REDESCRIBE_N more messages
            con.execute("UPDATE files SET about=?, about_n=? WHERE path=?", (about, row[6], row[0]))
            written += 1
    con.commit()
    con.close()
    return written
