"""iTerm2 gives the cursor's line counted with the scrollback; the bridge
makes it a screen line before it asks screen.prompt_state."""
import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import iterm  # noqa: E402
from claude_focus.screen import DRAFT, EMPTY  # noqa: E402

RULE = "─" * 60
# The linkmcp pane on 2026-10-24: Claude Code's grey suggestion in an empty
# prompt, the screen's first line is line 65023 of the pane.
SCREEN = ["report", "", RULE + " linkmcp ─", "❯ kaj je naslednje", RULE, "  Opus 5.5 | effort xhigh"]
FIRST = 65023


def pane(cursor):
    contents = NS(number_of_lines=len(SCREEN), line=lambda i: NS(string=SCREEN[i]),
                  cursor_coord=NS(x=cursor[0], y=FIRST + cursor[1]),
                  windowed_coord_range=NS(coordRange=NS(start=NS(y=FIRST))))
    return NS(async_get_screen_contents=mock.AsyncMock(return_value=contents))


class PromptStateTest(unittest.TestCase):
    def state(self, cursor):
        bridge = iterm.Bridge(None)
        bridge.session = lambda sid: pane(cursor)
        return asyncio.run(bridge.prompt_state("p1"))

    def test_suggestion_in_a_long_pane_is_empty(self):
        self.assertEqual(self.state((2, 3)), EMPTY)

    def test_draft_in_a_long_pane(self):
        self.assertEqual(self.state((18, 3)), DRAFT)


if __name__ == "__main__":
    unittest.main()
