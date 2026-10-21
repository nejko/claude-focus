import unittest

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus.screen import DRAFT, EMPTY, OTHER, prompt_state  # noqa: E402

RULE = "─" * 60
FOOTER = "\x00\x00Opus\x005.5 | effort xhigh\n\x00\x00⏵⏵\x00bypass\x00permissions\x00on"


class PromptStateTest(unittest.TestCase):
    def test_empty(self):
        text = f"report\x00text\n\n✻\x00Baked for 2m\n\n{RULE}\n❯\n{RULE}\n{FOOTER}"
        self.assertEqual(prompt_state(text), EMPTY)

    def test_draft(self):
        text = f"report\n{RULE}\n❯\xa0we\x00need\x00to\x00fix\x00this\n{RULE}\n{FOOTER}"
        self.assertEqual(prompt_state(text), DRAFT)

    def test_multiline_draft(self):
        text = f"report\n{RULE}\n❯\n  second line\n{RULE}\n{FOOTER}"
        self.assertEqual(prompt_state(text), DRAFT)

    def test_suggestion_text_with_cursor_at_start_is_empty(self):
        text = f"hi\n{RULE}\n❯ Try \"how does <filepath> work?\"\n{RULE}\n{FOOTER}"
        self.assertEqual(prompt_state(text, cursor=(2, 2)), EMPTY)
        self.assertEqual(prompt_state(text), DRAFT)  # without the cursor: be safe

    def test_draft_with_cursor_at_end(self):
        text = f"hi\n{RULE}\n❯ hello draft\n{RULE}\n{FOOTER}"
        self.assertEqual(prompt_state(text, cursor=(13, 2)), DRAFT)

    def test_open_question(self):
        text = (f"report\n{RULE}\n ☐ Shipping\n│ Where are you now?\n  1. Shipped\n"
                f"❯ 2. Booked, not handed over\n  5. Type something.\n{RULE}\n"
                "  6. Chat about this\nEnter to select · ↑/↓ to navigate · Esc to cancel")
        self.assertEqual(prompt_state(text), OTHER)

    def test_question_pointer_inside_box(self):
        text = f"x\n{RULE}\n❯ 1. Subscription only (Recommended)\n  2. With accounts\n{RULE}\n"
        self.assertEqual(prompt_state(text), OTHER)

    def test_named_session_border(self):
        named = "─" * 60 + " docs-rewrite-2 ─"
        self.assertEqual(prompt_state(f"report\n{named}\n❯\n{RULE}\n{FOOTER}"), EMPTY)
        self.assertEqual(prompt_state(f"report\n{named}\n❯ a draft\n{RULE}\n{FOOTER}"), DRAFT)
        self.assertEqual(prompt_state(f"report\n── a title ─\n❯\n{RULE}\n{FOOTER}"), OTHER)

    def test_shell(self):
        self.assertEqual(prompt_state("ana@mac ~ % "), OTHER)


if __name__ == "__main__":
    unittest.main()
