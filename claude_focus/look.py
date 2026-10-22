"""Colors and small drawing helpers for the focus screen. It uses 24-bit
color (iTerm2 has it). A theme (THEMES, config "theme") has one palette for
the terminal's dark appearance and one for its light one. In the default
theme each section has its own color: NEED YOU coral, YOUR TO-DOS blue,
PENDING amber, WORKING cyan, FREE grey, free terminals green. The screen
needs no iTerm2 profile for them."""
import re

from .themes import THEMES as MORE_THEMES

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
R, B = "\033[0m", "\033[1m"
EIGHTHS = "▏▎▍▌▋▊▉█"


def fg(c):
    return "\033[38;2;%d;%d;%dm" % c


def bg(c):
    return "\033[48;2;%d;%d;%dm" % c


def mix(c, base, a):
    """The color c at strength a (0 to 1) over the color base."""
    return tuple(round(base[i] + (c[i] - base[i]) * a) for i in range(3))


DARK = {
    "bg": (21, 25, 31),  # iTerm2's dark background
    "text": (222, 226, 232), "soft": (170, 177, 189), "dim": (124, 133, 147), "faint": (58, 65, 76),
    "track": (40, 45, 54), "key": (46, 52, 63), "alert": (255, 102, 102),
    "need": (255, 120, 112), "todo": (110, 162, 255), "pending": (232, 180, 92),
    "working": (90, 204, 222), "free": (146, 154, 168), "terminals": (98, 214, 110),
    "high": (255, 102, 102), "mid": (232, 180, 92), "low": (150, 158, 172),
}
LIGHT = {
    "bg": (250, 250, 250),
    "text": (34, 39, 48), "soft": (78, 86, 99), "dim": (104, 112, 125), "faint": (212, 216, 222),
    "track": (228, 231, 236), "key": (226, 230, 236), "alert": (200, 40, 40),
    "need": (204, 58, 52), "todo": (36, 98, 208), "pending": (168, 104, 0),
    "working": (0, 132, 152), "free": (118, 126, 138), "terminals": (28, 140, 56),
    "high": (200, 40, 40), "mid": (168, 104, 0), "low": (118, 126, 138),
}

THEMES = {"default": (DARK, LIGHT), **MORE_THEMES}  # name -> (dark, light); key t switches (themes.py)


def palette(appearance, theme="default"):
    """The palette of a theme (an unknown name: the default) for the
    terminal's appearance: iTerm2's effectiveTheme ("dark", "light",
    "light highContrast", ...), or macOS's in herdr."""
    dark, light = THEMES.get(theme) or THEMES["default"]
    return light if "light" in (appearance or "") else dark


def width(s):
    """Visible columns of a line with color codes (one column per character)."""
    return len(ANSI.sub("", s))


def upper(line):
    """A line's text in capitals, its color codes as they are."""
    out, pos = [], 0
    for m in ANSI.finditer(line):
        out += [line[pos:m.start()].upper(), m.group()]
        pos = m.end()
    return "".join(out) + line[pos:].upper()


def cut(text, n):
    """Plain text to at most n columns, with … where it was cut."""
    if n <= 0:
        return ""
    return text if len(text) <= n else text[:n - 1] + "…"


def cut_left(text, n):
    """Plain text to at most n columns, with … where its start was cut: a
    path keeps its end, the part that names the folder."""
    if n <= 0:
        return ""
    return text if len(text) <= n else "…" + text[len(text) - n + 1:]


def pad(s, n, right=False):
    """Pad a colored string to n visible columns (right=True: align right)."""
    gap = " " * max(0, n - width(s))
    return gap + s if right else s + gap


def fit(line, w):
    """Cut a line to w visible columns, keeping its color codes: a wrapped
    line would push the header off the top of the screen. The last column
    of a cut line shows …"""
    if width(line) <= w:
        return line
    out, n, i = [], 0, 0
    while i < len(line):
        m = ANSI.match(line, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        if n == w - 1:
            return "".join(out) + "…" + R
        out.append(line[i])
        n += 1
        i += 1
    return "".join(out)


def bar(value, cells, color, track):
    """A horizontal bar: value (0 to 100) as a length of `cells` columns, in
    eighths of a column, on a track of the full length."""
    eighths = round(max(0, min(100, value)) / 100 * cells * 8)
    full, part = divmod(eighths, 8)
    s = "█" * full + (EIGHTHS[part - 1] if part else "")
    return bg(track) + fg(color) + s + " " * (cells - len(s)) + R


def on(line, color, w):
    """The whole line (w columns) on a background color: the selected row."""
    body = pad(fit(line, w), w)
    return bg(color) + body.replace(R, R + bg(color)) + R
