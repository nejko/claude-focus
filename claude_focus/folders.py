"""The folders a new Claude session can start in, for the folder picker on a
free terminal: every folder that Claude Code ran in (its own list in
~/.claude.json) and the subfolders of the user's project folders (config
"project_roots"), the most recently used first. It reads names and times
only, never a file inside a project."""
import json
import os
import re
import stat

CLAUDE_JSON = os.path.expanduser("~/.claude.json")
TRANSCRIPTS = os.path.expanduser("~/.claude/projects")
TEMP = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")


def _encoded(path):
    """The name of a folder's transcript directory in ~/.claude/projects."""
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def _last_session(path, transcripts):
    """When a Claude session in this folder last wrote its transcript (0 = never)."""
    try:
        with os.scandir(os.path.join(transcripts, _encoded(path))) as it:
            return max((e.stat().st_mtime for e in it if e.name.endswith(".jsonl")), default=0)
    except OSError:
        return 0


def candidates(roots, claude_json=CLAUDE_JSON, transcripts=TRANSCRIPTS, skip=TEMP):
    """[(path, when, used)], newest first. used: a transcript of a Claude
    session in that folder is still there, and when is its last write; else
    when is the folder's own time (Claude Code deletes old transcripts).
    Folders that are gone, and temporary ones, are left out."""
    try:
        with open(claude_json) as f:
            paths = list(json.load(f).get("projects") or {})
    except (OSError, ValueError, AttributeError):
        paths = []
    paths = [p for p in paths if not (p + "/").startswith(skip)]
    for root in roots:
        try:
            paths += [e.path for e in os.scandir(os.path.expanduser(root)) if not e.name.startswith(".")]
        except OSError:
            continue
    # One row per folder: on macOS ~/projects is ~/Projects, and a link is its
    # target. The first spelling wins (Claude Code's own, then the roots in order).
    found, seen = [], set()
    for path in paths:
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not stat.S_ISDIR(st.st_mode) or (st.st_dev, st.st_ino) in seen:
            continue
        seen.add((st.st_dev, st.st_ino))
        when = _last_session(path, transcripts)
        found.append((path, when, True) if when else (path, st.st_mtime, False))
    return sorted(found, key=lambda f: -f[1])


def _subsequence(query, text):
    """The positions of query's letters in text, in order, or None."""
    out, i = [], 0
    for ch in query:
        i = text.find(ch, i)
        if i < 0:
            return None
        out.append(i)
        i += 1
    return out


def _abbreviation(query, text):
    """The positions of the query's letters, in order, in the folder's own
    name (the end of text), or None."""
    start = text.rstrip("/").rfind("/") + 1
    hit = _subsequence("".join(query.split()), text[start:])
    return None if hit is None else [start + i for i in hit]


def marks(text, query):
    """The positions in text that match the query: each word where it first
    occurs (any case), or the letters of a typed abbreviation."""
    low, words = text.lower(), query.lower().split()
    if all(w in low for w in words):
        return {i for w in words for i in range(low.index(w), low.index(w) + len(w))}
    return set(_abbreviation(" ".join(words), low) or ())


def search(folders, query, show=lambda p: p):
    """The folders that match the query, best first: the folder's own name
    starts with the first word, then has it, then the path has every word.
    Within each, the most recent first. When no path has every word, the
    letters typed are an abbreviation of the folder's name: "clfo" finds
    claude-focus. show(path) is the text that is searched (the path as the
    screen shows it)."""
    words = query.lower().split()
    if not words:
        return list(folders)
    texts = [show(f[0]).lower() for f in folders]
    ranked = []
    for n, (f, text) in enumerate(zip(folders, texts)):
        name = os.path.basename(text.rstrip("/"))
        if all(w in text for w in words):
            ranked.append((0 if name.startswith(words[0]) else 1 if words[0] in name else 2, n, f))
    if not ranked:
        ranked = [(0, n, f) for n, (f, text) in enumerate(zip(folders, texts))
                  if _abbreviation(" ".join(words), text) is not None]
    return [f for _, _, f in sorted(ranked)]
