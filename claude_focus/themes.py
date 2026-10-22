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
  rule    the character of the rules and dividers (default ─)
  status  a text at the right end of the key line (the keys give way)
  upper   True: all text in capitals
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

# Midnight Commander (its default skin): light grey on blue in single-line
# frames, the panel's title and the selection black on cyan, and the key bar
# like Norton Commander's.
MIDNIGHT = {
    "bg": (0, 0, 170),
    "text": (255, 255, 255), "soft": (170, 170, 170), "dim": (0, 170, 170), "faint": (0, 170, 170),
    "track": (0, 0, 110), "key": (0, 0, 0), "alert": (255, 85, 85),
    "need": (255, 255, 85), "todo": (255, 255, 255), "pending": (85, 255, 255),
    "working": (170, 170, 170), "free": (0, 170, 170), "terminals": (85, 255, 85),
    "high": (255, 85, 85), "mid": (255, 255, 85), "low": (0, 170, 170),
    "paint": True, "frame": (170, 170, 170), "box": SINGLE, "marker": " ",
    "title": ("~/claude-focus", (0, 0, 0), (0, 170, 170)),
    "select": ((0, 0, 0), (0, 170, 170)),
    "bar": ((0, 0, 0), (0, 170, 170)),
    "keys": {"cap": ((170, 170, 170), (0, 0, 0)), "label": ((0, 0, 0), (0, 170, 170)),
             "fmt": ("{}", "{} "), "sep": " ", "bg": (0, 0, 0)},
}

# Commodore 64 (the Pepto palette): light blue capitals on blue inside a thick
# light blue border, reverse video for the bars.
C64 = {
    "bg": (53, 40, 121),
    "text": (255, 255, 255), "soft": (108, 94, 181), "dim": (108, 94, 181), "faint": (80, 68, 150),
    "track": (40, 30, 95), "key": (108, 94, 181), "alert": (154, 103, 89),
    "need": (184, 199, 111), "todo": (112, 164, 178), "pending": (154, 103, 89),
    "working": (149, 149, 149), "free": (108, 94, 181), "terminals": (154, 210, 132),
    "high": (154, 103, 89), "mid": (184, 199, 111), "low": (108, 94, 181),
    "paint": True, "upper": True, "frame": (108, 94, 181), "box": "█" * 8, "marker": " ",
    "title": ("**** COMMODORE 64 BASIC V2 ****", (108, 94, 181), None),
    "select": ((53, 40, 121), (108, 94, 181)),
    "bar": ((53, 40, 121), (108, 94, 181)),
    "keys": {"cap": ((53, 40, 121), (108, 94, 181)), "label": ((108, 94, 181), None),
             "fmt": ("{}", " {}"), "sep": "  ", "bg": None},
}


# WordPerfect 5.1 (its default Setup colors): light grey on blue with bold in
# white, a block light grey on red, the menu bar blue on grey, the keys as a
# bottom-line menu ("1 Retrieve; 2 Delete"), and the status at the right.
WORDPERFECT = {
    "bg": (0, 0, 170),
    "text": (255, 255, 255), "soft": (170, 170, 170), "dim": (170, 170, 170), "faint": (85, 85, 255),
    "track": (0, 0, 110), "key": (0, 0, 170), "alert": (255, 85, 85),
    "need": (255, 255, 255), "todo": (255, 255, 85), "pending": (85, 255, 255),
    "working": (170, 170, 170), "free": (85, 85, 255), "terminals": (170, 170, 170),
    "high": (255, 255, 255), "mid": (255, 255, 85), "low": (170, 170, 170),
    "paint": True, "marker": " ",
    "select": ((170, 170, 170), (170, 0, 0)),
    "bar": ((0, 0, 170), (170, 170, 170)),
    "keys": {"cap": ((255, 255, 255), None), "label": ((170, 170, 170), None),
             "fmt": ("{}", " {}"), "sep": "; ", "bg": None},
    "status": 'Doc 1 Pg 1 Ln 1" Pos 1"',
}

# Award BIOS setup (colors sampled from a screenshot): yellow items on blue
# in a white double-line box under the CMOS Setup Utility title, a ► before
# the selected item, the selection white on red, the key help as Esc : Quit.
AWARD = {
    "bg": (0, 0, 170),
    "text": (255, 255, 85), "soft": (255, 255, 85), "dim": (170, 170, 170), "faint": (255, 255, 255),
    "track": (0, 0, 110), "key": (0, 0, 170), "alert": (255, 85, 85),
    "need": (255, 255, 255), "todo": (255, 255, 255), "pending": (85, 255, 255),
    "working": (85, 255, 85), "free": (170, 170, 170), "terminals": (85, 255, 85),
    "high": (255, 85, 85), "mid": (255, 255, 85), "low": (170, 170, 170),
    "paint": True, "frame": (255, 255, 255), "box": DOUBLE, "marker": "►",
    "title": ("CMOS Setup Utility - Copyright (C) 1984-1999 Award Software", (255, 255, 255), None),
    "select": ((255, 255, 255), (170, 0, 0)),
    "bar": ((255, 255, 255), (170, 0, 0)),
    "keys": {"cap": ((255, 255, 255), None), "label": ((255, 255, 255), None),
             "fmt": ("{} : ", "{}"), "sep": "   ", "bg": None},
    "status": "F10 : Save & Exit Setup",
}

def monitor(bg, bright, normal, dim, faint):
    """A monochrome monitor: one phosphor color in four strengths, reverse
    video for the bars."""
    return {"bg": bg, "text": bright, "soft": normal, "dim": dim, "faint": faint, "track": faint, "key": faint,
            "alert": bright, "need": bright, "todo": bright, "pending": normal, "working": normal,
            "free": dim, "terminals": normal, "high": bright, "mid": normal, "low": dim,
            "paint": True, "marker": " ", "select": (bg, bright), "bar": (bg, bright),
            "keys": {"cap": (bg, normal), "label": (normal, None), "fmt": ("{}", " {}"), "sep": "  ", "bg": None}}


GREEN = monitor((5, 12, 5), (51, 255, 51), (38, 190, 38), (26, 128, 26), (14, 60, 14))
AMBER = monitor((12, 8, 0), (255, 176, 0), (200, 138, 0), (140, 96, 0), (64, 44, 0))

# COBISS/OPAC as the libraries had it in the 1990s: a DEC VT420 or VT510
# terminal on IZUM's VAX, monochrome amber (what people remember), reverse
# video for the header and the bars, and the prompt Izbira:. No screenshot of
# the text-mode OPAC was found, so this is a reconstruction.
COBISS = dict(monitor((18, 12, 0), (255, 176, 0), (204, 136, 0), (140, 94, 0), (70, 47, 0)),
              keys={"cap": ((255, 176, 0), None), "label": ((204, 136, 0), None),
                    "fmt": ("{}", "={}"), "sep": "  ", "bg": None},
              status="Izbira: █")


def scheme(bg, text, soft, dim, faint, track, need, todo, pending, working, terminals, alert, mid, accent):
    """A modern color scheme: the default's drawing on the scheme's own
    background, its accent for the title bar while the window has the keys."""
    return {"bg": bg, "text": text, "soft": soft, "dim": dim, "faint": faint, "track": track, "key": faint,
            "alert": alert, "need": need, "todo": todo, "pending": pending, "working": working, "free": dim,
            "terminals": terminals, "high": alert, "mid": mid, "low": dim, "paint": True, "bar": (bg, accent)}


# Each scheme's own palette file: background, text, comments, accents.
DRACULA = scheme((40, 42, 54), (248, 248, 242), (208, 208, 202), (98, 114, 164), (68, 71, 90), (52, 55, 70),
                 (255, 121, 198), (189, 147, 249), (255, 184, 108), (139, 233, 253), (80, 250, 123),
                 (255, 85, 85), (241, 250, 140), (189, 147, 249))
SOLARIZED_DARK = scheme((0, 43, 54), (147, 161, 161), (131, 148, 150), (88, 110, 117), (7, 54, 66), (7, 54, 66),
                        (203, 75, 22), (38, 139, 210), (181, 137, 0), (42, 161, 152), (133, 153, 0),
                        (220, 50, 47), (181, 137, 0), (38, 139, 210))
SOLARIZED_LIGHT = scheme((253, 246, 227), (7, 54, 66), (88, 110, 117), (147, 161, 161), (238, 232, 213),
                         (238, 232, 213), (203, 75, 22), (38, 139, 210), (181, 137, 0), (42, 161, 152),
                         (133, 153, 0), (220, 50, 47), (181, 137, 0), (38, 139, 210))
NORD = scheme((46, 52, 64), (236, 239, 244), (216, 222, 233), (136, 146, 166), (67, 76, 94), (59, 66, 82),
              (208, 135, 112), (136, 192, 208), (235, 203, 139), (143, 188, 187), (163, 190, 140),
              (191, 97, 106), (235, 203, 139), (136, 192, 208))
GRUVBOX_DARK = scheme((40, 40, 40), (235, 219, 178), (213, 196, 161), (146, 131, 116), (80, 73, 69), (60, 56, 54),
                      (254, 128, 25), (131, 165, 152), (250, 189, 47), (142, 192, 124), (184, 187, 38),
                      (251, 73, 52), (250, 189, 47), (250, 189, 47))
GRUVBOX_LIGHT = scheme((251, 241, 199), (60, 56, 54), (80, 73, 69), (146, 131, 116), (213, 196, 161),
                       (235, 219, 178), (175, 58, 3), (7, 102, 120), (181, 118, 20), (66, 123, 88), (121, 116, 14),
                       (157, 0, 6), (181, 118, 20), (181, 118, 20))
TOKYO_NIGHT = scheme((26, 27, 38), (192, 202, 245), (169, 177, 214), (115, 122, 162), (59, 66, 97), (41, 46, 66),
                     (255, 158, 100), (122, 162, 247), (224, 175, 104), (125, 207, 255), (158, 206, 106),
                     (247, 118, 142), (224, 175, 104), (122, 162, 247))
CATPPUCCIN_MOCHA = scheme((30, 30, 46), (205, 214, 244), (186, 194, 222), (127, 132, 156), (69, 71, 90),
                          (49, 50, 68), (243, 139, 168), (137, 180, 250), (249, 226, 175), (137, 220, 235),
                          (166, 227, 161), (243, 139, 168), (250, 179, 135), (203, 166, 247))
CATPPUCCIN_LATTE = scheme((239, 241, 245), (76, 79, 105), (92, 95, 119), (140, 143, 161), (204, 208, 218),
                          (220, 224, 232), (210, 15, 57), (30, 102, 245), (223, 142, 29), (4, 165, 229),
                          (64, 160, 43), (210, 15, 57), (254, 100, 11), (136, 57, 239))

THEMES = {
    "norton": (NORTON, NORTON), "midnight-commander": (MIDNIGHT, MIDNIGHT), "qbasic": (QBASIC, QBASIC),
    "turbo-pascal": (TURBO_PASCAL, TURBO_PASCAL), "clipper": (CLIPPER, CLIPPER),
    "wordperfect": (WORDPERFECT, WORDPERFECT), "award-bios": (AWARD, AWARD),
    "total-commander": (TOTAL_COMMANDER, TOTAL_COMMANDER), "cobiss": (COBISS, COBISS), "c64": (C64, C64),
    "green-screen": (GREEN, GREEN), "amber-screen": (AMBER, AMBER), "claude-code": (CLAUDE_DARK, CLAUDE_LIGHT),
    "dracula": (DRACULA, DRACULA), "solarized": (SOLARIZED_DARK, SOLARIZED_LIGHT), "nord": (NORD, NORD),
    "gruvbox": (GRUVBOX_DARK, GRUVBOX_LIGHT), "tokyo-night": (TOKYO_NIGHT, TOKYO_NIGHT),
    "catppuccin": (CATPPUCCIN_MOCHA, CATPPUCCIN_LATTE),
}

ABOUT = {  # for the theme preview (key t)
    "default": "the default look", "norton": "Norton Commander", "midnight-commander": "Midnight Commander",
    "qbasic": "Microsoft QBasic 1.1", "turbo-pascal": "Borland Turbo Pascal 7", "clipper": "a Clipper application",
    "wordperfect": "WordPerfect 5.1", "award-bios": "Award BIOS setup", "total-commander": "Total Commander",
    "cobiss": "COBISS/OPAC on an amber DEC terminal (a reconstruction)", "c64": "Commodore 64", "green-screen": "a green phosphor monitor",
    "amber-screen": "an amber monitor", "claude-code": "Claude Code", "dracula": "Dracula",
    "solarized": "Solarized, dark or light", "nord": "Nord", "gruvbox": "Gruvbox, dark or light",
    "tokyo-night": "Tokyo Night", "catppuccin": "Catppuccin, Mocha or Latte",
}
