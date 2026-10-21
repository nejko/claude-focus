"""Colors and small drawing helpers for the focus screen. It uses 24-bit
color (iTerm2 has it). A theme (THEMES, config "theme") has one palette for
the terminal's dark appearance and one for its light one. In the default
theme each section has its own color: NEED YOU coral, YOUR TO-DOS blue,
PENDING amber, WORKING cyan, FREE grey, free terminals green. The screen
needs no iTerm2 profile for them."""
import re

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

# Norton Commander: the 16 colors of a DOS screen, the same in a dark and a
# light terminal. Besides the colors, the keys below say how App.render
# draws a theme; the default theme has none of them.
NORTON = {
    "bg": (0, 0, 170),
    "text": (255, 255, 255), "soft": (85, 255, 255), "dim": (0, 170, 170), "faint": (0, 170, 170),
    "track": (0, 0, 110), "key": (0, 0, 0), "alert": (255, 85, 85),
    "need": (255, 255, 85), "todo": (255, 255, 255), "pending": (255, 85, 255),
    "working": (85, 255, 255), "free": (0, 170, 170), "terminals": (85, 255, 85),
    "high": (255, 85, 85), "mid": (255, 255, 85), "low": (0, 170, 170),
    "paint": True,                         # the screen paints its own background (else the terminal's shows)
    "frame": (85, 255, 255),               # the list in a double-line frame of this color
    "select": ((0, 0, 0), (0, 170, 170)),  # the selected row all in these colors: black on cyan
    "bar": ((0, 0, 0), (0, 170, 170)),     # the title bar while the window has the keys
    "keys": ((255, 255, 255), (0, 0, 0), (0, 0, 0), (0, 170, 170)),  # a key white on black, its label black on cyan
    "keys_bg": (0, 0, 0),                  # the line of the keys
}
THEMES = {"default": (DARK, LIGHT), "norton": (NORTON, NORTON)}  # name -> (dark, light); key t switches


def palette(appearance, theme="default"):
    """The palette of a theme (an unknown name: the default) for the
    terminal's appearance: iTerm2's effectiveTheme ("dark", "light",
    "light highContrast", ...), or macOS's in herdr."""
    dark, light = THEMES.get(theme) or THEMES["default"]
    return light if "light" in (appearance or "") else dark


def width(s):
    """Visible columns of a line with color codes (one column per character)."""
    return len(ANSI.sub("", s))


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
