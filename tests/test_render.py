import contextlib
import io
import os
import re
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402
from claude_focus import look  # noqa: E402

STAMP = 1790915096936
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def need(name, ask, reason="", eff=50.0):
    s = {"sessionId": name, "key": name, "name": name, "pid": 1, "status": "idle", "statusSince": STAMP}
    sc = {"priority": 50, "kind": "decision", "ask": ask, "effort_min": 1, "reason": reason}
    return {"s": s, "e": {}, "score": sc, "fresh": sc, "eff": eff, "base": 50, "waited_h": 1.0}


def pending(name, note):
    s = {"sessionId": name, "key": name, "name": name, "pid": 1, "status": "idle", "statusSince": STAMP}
    p = {"until": STAMP + 3600000, "check": "curl -s https://example.com/status | grep -q done", "every_min": 10,
         "last_check": STAMP, "last_rc": 1, "note": note}
    return {"s": s, "e": {}, "score": None, "fresh": None, "pending": p, "wake": STAMP + 600000, "why": note}


def terminal(sid, tty, path="~"):
    return {"s": {"key": "pane:" + sid, "name": os.path.basename(path)}, "e": {}, "score": None,
            "fresh": None, "pane": sid, "tty": tty, "path": path, "job": "zsh"}


class LayoutTest(unittest.TestCase):
    def setUp(self):
        self.app = appmod.App(None)
        self.app.pane_of = lambda s: "pane"
        self.short = need("short", "Send it?")
        self.long = pending("long", "a very long note " * 30)

    def screen(self, items=(), selected=None, lines=40, terminals=(), raw=False):
        self.app.view = {"need": [i for i in items if "pending" not in i], "todo": [], "working": [],
                         "pending": [i for i in items if "pending" in i], "free": [], "terminals": list(terminals)}
        self.app.sel, self.app.last_frame = selected, None
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"COLUMNS": "120", "LINES": str(lines)}), \
                contextlib.redirect_stdout(out):
            self.app.render()
        lines = out.getvalue().replace("\033[K", "").split("\n")
        return lines if raw else [ANSI.sub("", x) for x in lines]

    def rows_at(self, screen, names):
        """The line of each name's row."""
        found = {}
        for i, line in enumerate(screen):
            for n in names:
                if f" {n} " in line[:60]:
                    found.setdefault(n, i)
        return found

    def test_rows_stay_in_place_when_the_selection_changes(self):
        items = [self.short, self.long]
        a, b = self.screen(items, "short"), self.screen(items, "long")
        self.assertEqual(self.rows_at(a, ["short", "long"]), self.rows_at(b, ["short", "long"]))
        self.assertEqual(len(a), len(b))

    def test_no_card_under_the_list(self):
        # The card between the two rules went unread; the list is all.
        screen = self.screen([self.short], "short")
        self.assertEqual(sum("Send it?" in x for x in screen), 1)
        self.assertEqual(sum(x.startswith("─") for x in screen), 1)  # one rule, above the keys

    def test_a_long_scrolled_list_keeps_its_rows_too(self):
        items = [need(f"s{n:02}", "Short?") for n in range(30)] + [self.long]
        a, b = self.screen(items, "s05", lines=30), self.screen(items, "s06", lines=30)
        self.assertEqual(self.rows_at(a, ["s00", "s05"]), self.rows_at(b, ["s00", "s05"]))

    def test_a_long_list_scrolls_to_the_selected_row(self):
        items = [need(f"s{n:02}", "Short?") for n in range(40)]
        screen = self.screen(items, "s39", lines=30)
        self.assertIn("s39", self.rows_at(screen, ["s39"]))
        self.assertTrue(any("more lines" in x for x in self.screen(items, "s00", lines=30)))

    def test_a_score_of_an_earlier_state_is_not_shown(self):
        # claude-focus-f3, 2026-10-13 10:28: the score named the question the user
        # had answered at 10:27:31; the new state was not scored yet.
        item = dict(need("f3", "The focus window needs a restart for this.", reason="r"), fresh=None)
        screen = self.screen([item], "f3")
        self.assertFalse(any("needs a restart" in x for x in screen))
        self.assertIn("scoring...", screen[self.rows_at(screen, ["f3"])["f3"]])

    def test_green_title_bar_only_while_the_window_has_the_keys(self):
        for focused in (True, False):
            self.app.focused = focused
            title = self.screen([self.short], raw=True)[0]
            self.assertEqual(appmod.FOCUS_BAR in title, focused)
            if focused:  # the bar fills the whole line (the screen is 119 columns wide)
                self.assertEqual(len(ANSI.sub("", title)), 119)

    def test_todo_row_shows_the_task_and_the_wait(self):
        item = dict(need("vendor-mail", "x"), needs="Review and send the draft", pending={"until": STAMP},
                    wake=STAMP, why="the CSV export")
        line = ANSI.sub("", self.app.row("todo", item, 300))
        self.assertIn("vendor-mail", line)
        self.assertIn("Review and send the draft", line)
        self.assertIn("then it waits for: the CSV export", line)

    def test_todos_have_their_own_section_and_are_selected_only_after_need_you(self):
        todo = dict(need("partner-call", "x"), needs="Accept the invitation", pending={"until": STAMP},
                    wake=STAMP, why="call on Friday")
        self.app.view = {"need": [self.short], "todo": [todo], "working": [], "pending": [], "free": []}
        self.app.sel = None
        self.assertEqual(self.app.selected()[0], "need")
        self.app.view["need"] = []
        self.assertEqual(self.app.selected(), ("todo", todo))
        self.app.view["need"] = [self.short]
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"COLUMNS": "120", "LINES": "40"}), contextlib.redirect_stdout(out):
            self.app.last_frame = None
            self.app.render()
        screen = [ANSI.sub("", x).strip() for x in out.getvalue().replace("\033[K", "").split("\n")]
        heads = [n for n, x in enumerate(screen) if x.startswith(("NEED YOU", "YOUR TO-DOS"))]
        self.assertEqual([screen[n].split()[0] for n in heads], ["NEED", "YOUR"])
        self.assertTrue(any("partner-call" in x and "Accept the invitation" in x for x in screen))

    def test_every_line_fits_the_width(self):
        woke = dict(need("w", "word " * 80), woke={"reason": "the time came", "why_not": "a draft", "at": STAMP})
        screen = self.screen([woke, self.long, need("x" * 60, "y")], "w", terminals=[terminal("a", "ttys002")])
        self.assertTrue(all(len(x) <= 119 for x in screen))

    def test_selected_row_is_highlighted_over_the_full_width(self):
        raw = self.screen([self.short, need("other", "Other?")], "other", raw=True)
        line = next(x for x in raw if " other " in ANSI.sub("", x))
        self.assertTrue(line.startswith("\033[48;2;"))  # a background color from the first column
        self.assertEqual(len(ANSI.sub("", line)), 119)
        self.assertTrue(ANSI.sub("", line).startswith("▌"))

    def test_priority_bar_length_follows_the_priority(self):
        for eff, cells in ((100, "████████"), (50, "████"), (0, "")):
            line = ANSI.sub("", self.app.row("need", need("n", "?", eff=eff), 200))
            self.assertIn(f"{eff:>3} {cells:<8}", line)

    def test_free_terminals_have_their_own_section_at_the_end(self):
        terms = [terminal("a", "ttys002"), terminal("b", "ttys009", "~/Projects/hotel-website")]
        screen = [x.strip() for x in self.screen([self.short, self.long], terminals=terms)]
        heads = [x.split()[0] for x in screen if x.startswith(("NEED", "PENDING", "AVAILABLE"))]
        self.assertEqual(heads, ["NEED", "PENDING", "AVAILABLE"])
        row = next(x for x in screen if "ttys009" in x)
        self.assertIn("hotel-website", row)
        self.assertIn("~/Projects/hotel-website", row)

    def test_every_row_shows_its_folder_in_one_column(self):
        a, b = need("api", "Deploy?"), pending("docs", "the tag")
        a["s"]["cwd"] = os.path.expanduser("~/code/api")
        b["s"]["cwd"] = "/srv/docs-site"
        screen = self.screen([a, b], terminals=[terminal("t", "ttys002", "~/code/web")])
        rows = [next(x for x in screen if f" {n} " in x[:60]) for n in ("api", "docs", "web")]
        cols = [x.index(p) for x, p in zip(rows, ("~/code/api", "/srv/docs-site", "~/code/web"))]
        self.assertEqual(len(set(cols)), 1)  # one column, after the name
        self.assertLess(rows[0].index("api"), cols[0])
        self.assertEqual(rows[2].count("~/code/web"), 1)  # not again in the text

    def test_working_row_names_its_folder_once(self):
        w = need("builder", "x")
        w["s"].update(status="busy", cwd=os.path.expanduser("~/code/builder"))
        self.app.view = {"working": [w]}
        wide = ANSI.sub("", self.app.row("working", w, 200))
        self.assertEqual(wide.count("~/code/builder"), 1)
        narrow = ANSI.sub("", self.app.row("working", w, 90))  # no folder column: the text names it
        self.assertEqual(narrow.count("~/code/builder"), 1)

    def test_a_long_folder_keeps_its_end(self):
        item = need("deep", "?")
        item["s"]["cwd"] = "/very/long/" + "x" * 60 + "/the-project"
        self.app.view = {"need": [item]}
        line = ANSI.sub("", self.app.row("need", item, 300))
        self.assertIn("…" + ("x" * 60 + "/the-project")[-(appmod.PATH_CELLS - 1):] + "  ", line)

    def test_nothing_needs_you(self):
        screen = self.screen([self.long], terminals=[terminal("a", "ttys002")])
        self.assertTrue(any("Nothing needs you now." in x for x in screen))
        self.assertFalse(any("Nothing needs you now." in x for x in self.screen([self.short])))

    def test_restore_list_shows_above_the_keys(self):
        self.app.mode = "restore"
        self.app.restore_lines = ["Last layout before iTerm2 restarted: 2 to open.", "  open  backend"]
        screen = self.screen([self.short])
        keys = next(n for n, x in enumerate(screen) if "open them" in x)
        self.assertEqual(screen[keys - 3:keys - 1], ["  Last layout before iTerm2 restarted: 2 to open.",
                                                     "    open  backend"])

    def test_light_theme_has_its_own_colors(self):
        self.app.pal = look.palette("light highContrast")
        raw = "\n".join(self.screen([self.short], "short", raw=True))
        self.assertIn(look.bg(look.mix(look.LIGHT["need"], look.LIGHT["bg"], 0.16)), raw)


class FormatTest(unittest.TestCase):
    def test_wait_fits_six_columns(self):
        for h, text in ((0.5, "30m"), (5.34, "5h20m"), (23.99, "23h59m"), (46.7, "1.9d"), (300, "12d")):
            self.assertEqual(appmod.fmt_wait(h), text)

    def test_fit_cuts_with_an_ellipsis_and_keeps_colors(self):
        line = look.fg((1, 2, 3)) + "abcdefghij" + look.R
        self.assertEqual(ANSI.sub("", look.fit(line, 5)), "abcd…")
        self.assertEqual(look.fit(line, 10), line)

    def test_bar_in_eighths(self):
        for value, cells in ((50, "█ "), (56.25, "█▏"), (75, "█▌"), (100, "██"), (0, "  ")):
            self.assertEqual(ANSI.sub("", look.bar(value, 2, (0, 0, 0), (9, 9, 9))), cells)


class TerminalTest(unittest.TestCase):
    """Free terminals: panes that the user switched to the green profile (Available)."""

    def setUp(self):
        self.app = appmod.App(None)
        self.app.bridge = mock.Mock()
        self.app.bridge.go = mock.AsyncMock(return_value=True)
        self.app.flash = mock.Mock()
        self.panes = [("own", "ttys000", "Available", "/Users/ana", "Python"),
                      ("p9", "ttys009", "Available", os.path.expanduser("~"), "zsh"),
                      ("p2", "ttys002", "Available", os.path.expanduser("~/Projects/x"), "zsh"),
                      ("todo", "ttys004", "TODO", "/x", "zsh"),
                      ("taken", "ttys012", "Available", "/y", "2.1.304")]
        self.app.bridge.pane_info = mock.AsyncMock(return_value=self.panes)
        self.app.sessions = [{"pid": 7, "sessionId": "c", "key": "c", "tty": "ttys012"},
                             {"pid": 8, "sessionId": "bg", "key": "bg", "tty": ""}]

    def free(self):
        import asyncio
        with mock.patch.object(appmod, "own_pane", return_value="own"):
            return asyncio.run(self.app._free_terminals())

    def test_only_green_panes_without_a_claude_session(self):
        terms = self.free()
        self.assertEqual([t["pane"] for t in terms], ["p2", "p9"])  # oldest tty first
        self.assertEqual([(t["s"]["name"], t["path"]) for t in terms], [("x", "~/Projects/x"), ("~", "~")])

    def test_another_profile_name_in_the_config(self):
        self.app.cfg = dict(self.app.cfg, free_profile="TODO")
        self.assertEqual([t["pane"] for t in self.free()], ["todo"])

    def keys(self, *keys, sel=None):
        import asyncio
        self.app.view = {"need": [need("a", "?")], "todo": [], "working": [], "pending": [], "free": [],
                         "terminals": self.free()}
        self.app.sel = sel

        async def run():
            for k in keys:
                await self.app.on_key(k)
        asyncio.run(run())

    def test_enter_on_a_terminal_row_goes_to_its_pane(self):
        self.keys("\r", sel="pane:p9")
        self.app.bridge.go.assert_awaited_once_with("p9", zoom=True)
        self.assertIsNone(self.app.sel)

    def test_n_goes_to_the_first_free_terminal(self):
        self.keys("n")
        self.app.bridge.go.assert_awaited_once_with("p2", zoom=True)

    def test_n_without_a_free_terminal(self):
        self.app.bridge.pane_info = mock.AsyncMock(return_value=[])
        self.keys("n")
        self.app.bridge.go.assert_not_awaited()
        self.assertIn("Available", self.app.flash.call_args[0][0])

    def test_session_keys_do_nothing_on_a_terminal(self):
        with mock.patch.object(appmod.store, "update_state") as write:
            self.keys("s", "d", "u", "p", "r", sel="pane:p9")
        write.assert_not_called()
        self.assertEqual(self.app.mode, "main")


if __name__ == "__main__":
    unittest.main()
