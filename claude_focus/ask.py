"""The one-line "what does this session need from the user" text, taken from
the session itself (jev chooses and scores; it does not write text).

Order: an open question on the screen, an open AskUserQuestion in the
transcript, the first item under a "needed from you" style heading, a
"nothing needs you" sentence, Claude Code's recap line, then the first
sentence of the last message (it works best when sessions lead with the
outcome). The headings and sentences are matched in English and Slovenian."""
import re

from .screen import clean

MAX = 120
HEADINGS = re.compile(
    r"^\s*(?:#+\s*|\*\*)?\s*(needed from you|still open|what remains(?: for you)?|"
    r"what you need to do|your next step|next steps?|open items?|to do for you|"
    r"kaj (?:rabim|potrebujem) od (?:tebe|vas)|odprto|naslednji korak)\b[^\n]*$",
    re.IGNORECASE | re.MULTILINE)
NOTHING = re.compile(
    r"(nothing (?:else )?needs your (?:input|attention)[^.\n]*|nothing (?:is )?needed from you[^.\n]*|"
    r"no action (?:is )?needed[^.\n]*|nič ne čaka na (?:tebe|vas)[^.\n]*)", re.IGNORECASE)
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def _tidy(text):
    text = re.sub(r"\*\*|`", "", text)
    text = re.sub(r"\s+", " ", text).strip(" :-")
    return text if len(text) <= MAX else text[:MAX - 1].rstrip() + "…"


def screen_question(screen):
    """The question text of an open AskUserQuestion (lines that start with
    '│' above the options), or a permission prompt."""
    lines = [clean(x) for x in screen.splitlines()]
    if any("Enter to select" in x for x in lines[-6:]):
        # Only inside the question dialog; table rows ("│ a │ b │") are not questions.
        q = [x.strip()[1:].strip() for x in lines
             if x.strip().startswith("│") and x.count("│") == 1]
        if q:
            return _tidy(" ".join(q))
    for x in reversed(lines):
        if "Do you want to" in x and x.strip().endswith("?"):
            return _tidy("Permission prompt: " + x.strip())
    return ""


def _after_heading(text):
    m = HEADINGS.search(text)
    if not m:
        return ""
    rest = text[m.end():].splitlines()
    for line in rest:
        if not line.strip():
            continue
        if HEADINGS.match(line):
            continue
        return _tidy(BULLET.sub("", line))
    return ""


def _recap(screen):
    m = re.search(r"recap:\s*(.+?)(?:\(disable recaps|\n\s*\n|$)", screen.replace("\n", " \n"), re.S)
    return _tidy(m.group(1)) if m else ""


def _first_sentence(text):
    for para in text.split("\n"):
        para = BULLET.sub("", para).strip()
        if para and not para.startswith(("#", "|", "```", "...")):
            m = re.match(r"(.+?[.!?])(\s|$)", para)
            return _tidy(m.group(1) if m else para)
    return ""


def extract(screen, last_text, status):
    """Best one-line ask for the row."""
    if status == "waiting":
        q = screen_question(screen)
        if q:
            return q
    if "OPEN QUESTIONS:" in last_text:
        first = last_text.split("OPEN QUESTIONS:", 1)[1].strip().splitlines()
        if first:
            return _tidy(BULLET.sub("", first[0]).split(" [")[0])
    body = last_text.split("OPEN QUESTIONS:", 1)[0]
    for fn in (lambda: _after_heading(body),
               lambda: _tidy(NOTHING.search(body).group(1)) if NOTHING.search(body) else "",
               lambda: _recap(screen),
               lambda: _first_sentence(body)):
        out = fn()
        if out:
            return out
    return ""


def company(cwd, companies):
    """The company or area, from the project folder name and the config
    "companies" (folder name prefix -> label; the longest prefix wins)."""
    name = cwd.rstrip("/").rsplit("/", 1)[-1].lower()
    for prefix in sorted(companies, key=len, reverse=True):
        if name.startswith(prefix.lower()):
            return companies[prefix]
    return ""
