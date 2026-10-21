"""The focus screen's themes besides the default one (look.DARK and
look.LIGHT): name -> (palette in a dark terminal, palette in a light one).
A palette has the colors of look.DARK, and these keys say how App.render
draws it (the default palettes have none of them):

  paint   True: the screen paints its own background, "bg"
  frame   the list in a box of this color
  box     the box's characters: top-left, line, top-right, side,
          bottom-left, bottom-right, and the two ends of a divider (DOUBLE)
  title   (text, fg, bg): a title in the middle of the box's top line
  select  (fg, bg): the selected row all in one color, on a bar of bg
          (None: no bar)
  marker  the character at the left of the selected row (default ▌)
  bar     (fg, bg): the title line while the window has the keys
  keys    the line of the keys: {"cap": (fg, bg), "label": (fg, bg),
          "fmt": (cap format, label format), "sep": between two keys,
          "bg": the line's background}; a bg of None leaves the line's own
"""
DOUBLE, SINGLE, ROUND = "╔═╗║╚╝╟╢", "┌─┐│└┘├┤", "╭─╮│╰╯├┤"

# Norton Commander: the 16 colors of a DOS screen, the same in a dark and a
# light terminal.
NORTON = {
    "bg": (0, 0, 170),
    "text": (255, 255, 255), "soft": (85, 255, 255), "dim": (0, 170, 170), "faint": (0, 170, 170),
    "track": (0, 0, 110), "key": (0, 0, 0), "alert": (255, 85, 85),
    "need": (255, 255, 85), "todo": (255, 255, 255), "pending": (255, 85, 255),
    "working": (85, 255, 255), "free": (0, 170, 170), "terminals": (85, 255, 85),
    "high": (255, 85, 85), "mid": (255, 255, 85), "low": (0, 170, 170),
    "paint": True, "frame": (85, 255, 255), "box": DOUBLE, "marker": " ",
    "select": ((0, 0, 0), (0, 170, 170)),  # black on cyan
    "bar": ((0, 0, 0), (0, 170, 170)),
    "keys": {"cap": ((255, 255, 255), (0, 0, 0)), "label": ((0, 0, 0), (0, 170, 170)),
             "fmt": ("{}", " {} "), "sep": "   ", "bg": (0, 0, 0)},  # 1Help 2Menu: white on black, black on cyan
}

# Microsoft QBasic 1.1 (and MS-DOS Edit; colors from Microsoft's MS-DOS 6.0
# source): light grey on blue in a single-line window titled Untitled in
# reverse, the menu bar black on grey, a selected list item light grey on
# black, the status line white on cyan as <Shift+F1=Help> <F6=Window>.
QBASIC = {
    "bg": (0, 0, 170),
    "text": (255, 255, 255), "soft": (170, 170, 170), "dim": (170, 170, 170), "faint": (85, 85, 255),
    "track": (0, 0, 110), "key": (0, 0, 0), "alert": (255, 85, 85),
    "need": (255, 255, 255), "todo": (85, 255, 255), "pending": (255, 255, 85),
    "working": (170, 170, 170), "free": (85, 85, 255), "terminals": (85, 255, 85),
    "high": (255, 85, 85), "mid": (255, 255, 85), "low": (170, 170, 170),
    "paint": True, "frame": (170, 170, 170), "box": SINGLE, "marker": " ",
    "title": ("Untitled", (0, 0, 170), (170, 170, 170)),
    "select": ((170, 170, 170), (0, 0, 0)),
    "bar": ((0, 0, 0), (170, 170, 170)),
    "keys": {"cap": ((255, 255, 255), None), "label": ((255, 255, 255), None),
             "fmt": ("<{}=", "{}>"), "sep": " ", "bg": (0, 170, 170)},
}

# Borland Turbo Pascal 7 (Turbo Vision; colors from TURBO.EXE 7.01): yellow
# text on blue in a white double-line window, the menu bar and the status
# line black on grey with red key names, the selected menu item black on green.
TURBO_PASCAL = {
    "bg": (0, 0, 170),
    "text": (255, 255, 85), "soft": (255, 255, 255), "dim": (170, 170, 170), "faint": (85, 85, 255),
    "track": (0, 0, 110), "key": (170, 170, 170), "alert": (255, 85, 85),
    "need": (255, 255, 255), "todo": (85, 255, 255), "pending": (85, 255, 85),
    "working": (170, 170, 170), "free": (85, 85, 255), "terminals": (85, 255, 85),
    "high": (255, 85, 85), "mid": (255, 255, 85), "low": (170, 170, 170),
    "paint": True, "frame": (255, 255, 255), "box": DOUBLE, "marker": " ",
    "title": ("FOCUS.PAS", (255, 255, 255), None),
    "select": ((0, 0, 0), (0, 170, 0)),
    "bar": ((0, 0, 0), (170, 170, 170)),
    "keys": {"cap": ((170, 0, 0), (170, 170, 170)), "label": ((0, 0, 0), (170, 170, 170)),
             "fmt": ("{}", " {}"), "sep": "  ", "bg": (170, 170, 170)},
}

# A Clipper business application (SET COLOR TO "W+/B,N/W", the most common
# one): bright white on blue, double-line boxes (B_DOUBLE), the highlight bar
# and the status line at the bottom black on grey (N/W), a W+/R title bar.
CLIPPER = {
    "bg": (0, 0, 170),
    "text": (255, 255, 255), "soft": (170, 170, 170), "dim": (170, 170, 170), "faint": (85, 85, 255),
    "track": (0, 0, 110), "key": (170, 170, 170), "alert": (255, 85, 85),
    "need": (255, 255, 85), "todo": (85, 255, 255), "pending": (255, 85, 255),
    "working": (85, 255, 85), "free": (170, 170, 170), "terminals": (85, 255, 85),
    "high": (255, 85, 85), "mid": (255, 255, 85), "low": (170, 170, 170),
    "paint": True, "frame": (255, 255, 255), "box": DOUBLE, "marker": "►",
    "select": ((0, 0, 0), (170, 170, 170)),
    "bar": ((255, 255, 255), (170, 0, 0)),
    "keys": {"cap": ((0, 0, 0), (170, 170, 170)), "label": ((0, 0, 0), (170, 170, 170)),
             "fmt": ("{}", "-{}"), "sep": "  ", "bg": (170, 170, 170)},
}

# Total Commander on classic Windows: black on white panels, marked files red,
# the title bar and the (inverse) cursor bar white on navy, and grey buttons
# for the keys.
TOTAL_COMMANDER = {
    "bg": (255, 255, 255),
    "text": (0, 0, 0), "soft": (0, 0, 0), "dim": (128, 128, 128), "faint": (192, 192, 192),
    "track": (224, 224, 224), "key": (192, 192, 192), "alert": (255, 0, 0),
    "need": (255, 0, 0), "todo": (0, 0, 128), "pending": (128, 0, 128),
    "working": (0, 128, 128), "free": (128, 128, 128), "terminals": (0, 128, 0),
    "high": (255, 0, 0), "mid": (192, 128, 0), "low": (128, 128, 128),
    "paint": True, "frame": (128, 128, 128), "box": SINGLE, "marker": " ",
    "select": ((255, 255, 255), (0, 0, 128)),
    "bar": ((255, 255, 255), (0, 0, 128)),
    "keys": {"cap": ((0, 0, 0), (192, 192, 192)), "label": ((0, 0, 0), (192, 192, 192)),
             "fmt": (" {}", " {} "), "sep": "│", "bg": (192, 192, 192)},
}

# Claude Code (its dark and light themes, colors from the 2.1.306 binary):
# its colors on the terminal's background, claude orange for ✻ and what needs
# you, the suggestion blue of a selected option with its ❯, a rounded box
# like its panels, and plain key hints.
CLAUDE_DARK = {
    "bg": (21, 25, 31),
    "text": (255, 255, 255), "soft": (200, 200, 200), "dim": (153, 153, 153), "faint": (80, 80, 80),
    "track": (55, 55, 55), "key": (46, 52, 63), "alert": (255, 107, 128),
    "need": (215, 119, 87), "todo": (177, 185, 249), "pending": (255, 193, 7),
    "working": (175, 135, 255), "free": (153, 153, 153), "terminals": (78, 186, 101),
    "high": (255, 107, 128), "mid": (255, 193, 7), "low": (153, 153, 153),
    "frame": (136, 136, 136), "box": ROUND, "marker": "❯",
    "title": ("✻ claude-focus", (215, 119, 87), None),
    "select": ((177, 185, 249), None),
    "bar": ((0, 0, 0), (215, 119, 87)),
    "keys": {"cap": ((255, 255, 255), None), "label": ((153, 153, 153), None),
             "fmt": ("{}", " {}"), "sep": " · ", "bg": None},
}
CLAUDE_LIGHT = dict(CLAUDE_DARK, **{
    "bg": (250, 250, 250),
    "text": (0, 0, 0), "soft": (60, 60, 60), "dim": (102, 102, 102), "faint": (175, 175, 175),
    "track": (225, 225, 225), "key": (226, 230, 236), "alert": (171, 43, 63),
    "need": (215, 119, 87), "todo": (87, 105, 247), "pending": (150, 108, 30),
    "working": (135, 0, 255), "free": (102, 102, 102), "terminals": (44, 122, 57),
    "high": (171, 43, 63), "mid": (150, 108, 30), "low": (102, 102, 102),
    "frame": (138, 138, 138), "select": ((87, 105, 247), None),
    "bar": ((255, 255, 255), (215, 119, 87)),
    "keys": {"cap": ((0, 0, 0), None), "label": ((102, 102, 102), None),
             "fmt": ("{}", " {}"), "sep": " · ", "bg": None},
})

THEMES = {"norton": (NORTON, NORTON), "qbasic": (QBASIC, QBASIC), "turbo-pascal": (TURBO_PASCAL, TURBO_PASCAL),
          "clipper": (CLIPPER, CLIPPER), "total-commander": (TOTAL_COMMANDER, TOTAL_COMMANDER),
          "claude-code": (CLAUDE_DARK, CLAUDE_LIGHT)}
