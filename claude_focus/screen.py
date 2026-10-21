"""Read Claude Code's screen: is the prompt box empty, holding a draft, or
replaced by something else (a question, a permission prompt)?"""

import re

EMPTY, DRAFT, OTHER = "empty", "draft", "other"
NAMED_RULE = re.compile(r"─+ [^─]+ ─+")


def clean(line):
    """iTerm reports blank cells as NUL; the prompt uses a no-break space."""
    return line.replace("\x00", " ").replace("\xa0", " ").rstrip()


def _is_rule(line):
    """A full-width rule. In a named session, the top rule of the input box
    carries the name: `──────── docs-rewrite-2 ─`."""
    s = line.strip()
    return s.count("─") >= 20 and (set(s) == {"─"} or NAMED_RULE.fullmatch(s) is not None)


def prompt_state(text, cursor=None):
    """EMPTY when the input box is `❯` with nothing typed after it, DRAFT when
    it holds typed text, OTHER when the bottom of the screen is not an input
    box (an open question, a dialog, a shell). Only EMPTY is safe to type into.

    An empty prompt can show grey suggestion text (`❯ Try "..."`), which the
    screen text cannot tell from a draft. `cursor` = (x, y) in `text`'s line
    numbers settles it: in an empty prompt the cursor sits right after `❯`."""
    lines = [clean(x) for x in text.splitlines()]
    rules = [i for i, x in enumerate(lines) if _is_rule(x)]
    if len(rules) < 2:
        return OTHER
    top, bottom = rules[-2], rules[-1]
    box = lines[top + 1:bottom]
    if not box or not box[0].lstrip().startswith("❯"):
        return OTHER
    first = box[0].lstrip()[1:].strip()
    # "❯ 1. Option" is the selection pointer of a question, not the input box
    if first[:2].rstrip(".").isdigit() and first[1:3].startswith("."):
        return OTHER
    typed = first + "".join(x.strip() for x in box[1:])
    if not typed:
        return EMPTY
    if cursor is not None and len(box) == 1:
        x, y = cursor
        if y == top + 1 and x <= box[0].index("❯") + 2:
            return EMPTY  # only suggestion text; nothing typed
    return DRAFT
