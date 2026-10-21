import unittest

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import ask, jev, store  # noqa: E402

RULE = "─" * 60
QUESTION_SCREEN = (
    f"  - Package target: Fri 2 Oct.\n{RULE}\n"
    "←  ☐ Database  ☐ Backups  ✔ Submit  →\n"
    "│ Which database should the staging setup use (a copy of production or an empty one)?\n"
    "❯ 1. A copy of production (Recommended)\n  2. An empty database, you seed it\n"
    f"{RULE}\n  5. Chat about this\nEnter to select · Tab/Arrow keys to navigate · Esc to cancel")
NEEDED = """- **Save state:** the blog shows "Draft - saved".

## Needed from you

- **Publish or not:** read the draft and decide. I publish only when you tell me to.
- **Feed post:** after the article is published, you can use the short post."""
NOTHING_SL = """- **Evidenca:** vrstico sem dodal v `state/payments.csv`.

## Nič ne čaka na tebe

Avtomatika vsak priliv javi samo enkrat."""
NOTHING_EN = """**Next: the launch check on 1 October at 09:00** (in your calendar):
- Remove the search-engine block and deploy.

Nothing needs your input before then."""
COMPANIES = {"acme": "Acme", "acme-web": "Acme Web", "home": "Personal"}


class AskTest(unittest.TestCase):
    def test_open_question_on_screen(self):
        self.assertTrue(ask.extract(QUESTION_SCREEN, "", "waiting").startswith(
            "Which database should the staging setup use"))

    def test_table_rows_are_not_a_question(self):
        screen = "│ When │ You │ Time │\n│ Today │ Add the key │ 5 min │\n❯\n"
        self.assertEqual(ask.screen_question(screen), "")

    def test_open_question_from_transcript(self):
        last = "Report.\n\nOPEN QUESTIONS:\n- Send it now? [Send it now (Recommended) / I send it myself]"
        self.assertEqual(ask.extract("", last, "idle"), "Send it now?")

    def test_needed_from_you(self):
        self.assertEqual(ask.extract("", NEEDED, "idle"),
                         "Publish or not: read the draft and decide. I publish only when you tell me to.")

    def test_nothing_needed(self):
        self.assertEqual(ask.extract("", NOTHING_SL, "idle"), "Nič ne čaka na tebe")
        self.assertEqual(ask.extract("", NOTHING_EN, "idle"), "Nothing needs your input before then")

    def test_recap_then_first_sentence(self):
        screen = ("✻ Baked for 2m\n\n※ recap: We're getting the website relaunch started. "
                  "Next: read the draft once. (disable recaps in /config)")
        self.assertEqual(ask.extract(screen, "The follow-up is updated.", "idle"),
                         "We're getting the website relaunch started. Next: read the draft once.")
        self.assertEqual(ask.extract("", "The follow-up is updated. More text.", "idle"),
                         "The follow-up is updated.")

    def test_long_text_is_cut(self):
        self.assertLessEqual(len(ask.extract("", "x" * 500, "idle")), ask.MAX)

    def test_company(self):
        self.assertEqual(ask.company("/Users/ana/Projects/acme-api", COMPANIES), "Acme")
        self.assertEqual(ask.company("/Users/ana/Projects/acme-web-shop", COMPANIES), "Acme Web")  # longest prefix
        self.assertEqual(ask.company("/Users/ana/Projects/Home-taxes", COMPANIES), "Personal")
        self.assertEqual(ask.company("/Users/ana/Projects", COMPANIES), "")
        self.assertEqual(ask.company("/Users/ana/Projects/acme-api", {}), "")


class JevMappingTest(unittest.TestCase):
    def test_to_result(self):
        answers = {
            "priority": {"type": "score", "score": 3.93, "confidence": 0.94,
                         "probabilities": {"0": 0.0, "1": 0.0, "2": 0.0, "3": 0.05, "4": 0.95}},
            "kind": {"type": "choice", "choice": "decision", "confidence": 0.9},
            "effort": {"type": "choice", "choice": "5", "confidence": 0.8},
        }
        sess = {"status": "waiting", "cwd": "/Users/ana/Projects/acme-api"}
        r = jev.to_result(answers, sess, QUESTION_SCREEN, "", 2800, "jev-1.13.0", COMPANIES)
        self.assertEqual(r["priority"], 98)
        self.assertEqual(r["kind"], "decision")
        self.assertEqual(r["effort_min"], 5)
        self.assertEqual(r["company"], "Acme")
        self.assertIn("drop everything (95%)", r["reason"])
        self.assertAlmostEqual(r["cost_usd"], 2800 * 0.042 / 1e6)
        self.assertTrue(r["ask"].startswith("Which database"))

    def test_no_companies_configured(self):
        answers = {"priority": {"score": 2.0, "probabilities": {"2": 1.0}},
                   "kind": {"choice": "info"}, "effort": {"choice": "1"}}
        r = jev.to_result(answers, {"status": "idle", "cwd": "/Users/ana/Projects/acme-api"}, "", "", 100, "m")
        self.assertEqual(r["company"], "")

    def test_main_answer_is_kept(self):
        answers = {"priority": {"score": 0.4, "probabilities": {"0": 0.66, "1": 0.34}},
                   "kind": {"choice": "action"}, "effort": {"choice": "1"}}
        r = jev.to_result(answers, {"status": "idle", "cwd": "/x"}, "", "", 100, "m")
        self.assertEqual((r["level"], r["level_p"], r["kind"]), (0, 0.66, "action"))

    def test_int_probability_keys(self):
        answers = {"priority": {"score": 3.0, "probabilities": {0: 0.0, 3: 0.8, 4: 0.2}},
                   "kind": {"choice": "action"}, "effort": {"choice": "1"}}
        r = jev.to_result(answers, {"status": "idle", "cwd": "/x"}, "", "", 100, "m")
        self.assertIn("today (80%)", r["reason"])

    def test_priority_bounds(self):
        answers = {"priority": {"score": 0.0, "probabilities": {"0": 1.0}},
                   "kind": {"choice": "nothing"}, "effort": {"choice": "1"}}
        r = jev.to_result(answers, {"status": "idle", "cwd": "/x"}, "", "", 100, "m")
        self.assertEqual(r["priority"], 1)
        self.assertEqual(r["ask"], store.say(jev.KINDS["nothing"]))
        self.assertEqual(r["ask"], "The session is done or says that nothing is needed from the user")


if __name__ == "__main__":
    unittest.main()
