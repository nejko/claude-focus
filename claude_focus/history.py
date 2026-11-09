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

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, session TEXT, cwd TEXT, entrypoint TEXT,
    done INTEGER, ino INTEGER, name TEXT, title TEXT, prompt TEXT, last_at REAL);
CREATE VIRTUAL TABLE IF NOT EXISTS texts USING fts5(session UNINDEXED, at UNINDEXED, body,
    tokenize='unicode61 remove_diacritics 2');
"""


def connect(db=DB):
    con = sqlite3.connect(db, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")  # the focus window reads while the index writes
    con.executescript(SCHEMA)
    return con


def _when(stamp):
    """Unix s of an ISO timestamp such as 2026-10-09T18:28:25.431Z, or None."""
    try:
        return time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
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
    row = con.execute("SELECT done, ino, cwd, entrypoint, name, title, prompt, last_at FROM files WHERE path=?",
                      (path,)).fetchone()
    if row and row[1] == st.st_ino and row[0] == st.st_size:
        return False
    if row and row[1] == st.st_ino and row[0] < st.st_size:
        start, (cwd, entry, name, title, prompt, last_at) = row[0], row[2:]
    else:
        con.execute("DELETE FROM texts WHERE session=?", (session,))
        start, cwd, entry, name, title, prompt, last_at = 0, None, None, None, None, None, None
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
                    rows.append((session, at, text))
                if at:
                    last_at = max(last_at or 0, at)
    con.executemany("INSERT INTO texts (session, at, body) VALUES (?, ?, ?)", rows)
    con.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (path, session, cwd, entry, done, st.st_ino, name, title, prompt, last_at or st.st_mtime))
    con.commit()
    return True


def sync(transcripts=TRANSCRIPTS, db=DB):
    """Bring the index up to date: every transcript in transcripts/<folder>/,
    and away with the ones Claude Code deleted. Returns how many changed.
    One run at a time (a lock file): two would add the same messages twice."""
    with open(db + ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0  # another run is at it
        return _sync(transcripts, db)


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


def label(name, title, prompt):
    return name or title or (prompt or "").strip().splitlines()[0][:120] if (name or title or prompt) else "(no title)"


def search(query, db=DB, limit=30):
    """Past sessions that match every word of the query, as dicts with
    session, cwd, label, last_at and snippet (the matching text, marked with
    \\x01 and \\x02; None when the name, title or folder matched). Those
    whose name, title, last prompt or folder match come first, then those
    whose conversation does; each group newest first."""
    words = fold(query).split()
    if not words or not os.path.exists(db):
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        files = {r[0]: r for r in con.execute(
            "SELECT session, cwd, entrypoint, name, title, prompt, last_at FROM files")
            if r[2] in RESUMABLE and r[1]}
        head, seen = [], set()
        for s, cwd, _, name, title, prompt, last_at in files.values():
            text = fold(" ".join(x for x in (name, title, prompt, os.path.basename(cwd)) if x))
            if all(w in text for w in words):
                head.append((-(last_at or 0), s, None))
                seen.add(s)
        match = " AND ".join('"' + w.replace('"', '""') + '"*' for w in words)
        body = []
        try:
            for s, at, snip in con.execute(
                    "SELECT session, at, snippet(texts, 2, char(1), char(2), '…', 12) FROM texts "
                    "WHERE texts MATCH ? ORDER BY at DESC LIMIT 2000", (match,)):
                if s in files and s not in seen:
                    seen.add(s)
                    body.append((-(files[s][6] or 0), s, re.sub(r"\s+", " ", snip)))
        except sqlite3.OperationalError:  # a query FTS5 cannot parse
            pass
    finally:
        con.close()
    out = []
    for _, s, snip in sorted(head) + sorted(body):
        _, cwd, _, name, title, prompt, last_at = files[s]
        out.append({"session": s, "cwd": cwd, "label": label(name, title, prompt), "last_at": last_at,
                    "snippet": snip})
    return out[:limit]
