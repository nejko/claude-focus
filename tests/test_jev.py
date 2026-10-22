import datetime as dt
import unittest

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import ask, jev, scorer, store  # noqa: E402

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


class KeyTest(unittest.TestCase):
    """A TypeSafe key goes to TypeSafe; an OpenRouter key to OpenRouter."""

    def key(self, env, file=""):
        import os
        import tempfile
        from unittest import mock
        with tempfile.NamedTemporaryFile("w", suffix=".credentials") as f:
            f.write(file)
            f.flush()
            clean = {k: v for k, v in os.environ.items()
                     if k not in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY")}
            with mock.patch.dict(os.environ, dict(clean, **env), clear=True), \
                    mock.patch.object(jev, "CREDENTIALS", f.name):
                return jev.api_key()

    def test_typesafe(self):
        self.assertEqual(self.key({"TYPESAFE_API_KEY": "ts"}), ("ts", None))
        self.assertEqual(self.key({}, "TYPESAFE_API_KEY=ts\n"), ("ts", None))

    def test_openrouter(self):
        self.assertEqual(self.key({"OPENROUTER_API_KEY": "or"}), ("or", jev.OPENROUTER))
        self.assertEqual(self.key({}, "# jev\nOPENROUTER_API_KEY=or\n"), ("or", jev.OPENROUTER))

    def test_typesafe_wins(self):
        self.assertEqual(self.key({"OPENROUTER_API_KEY": "or"}, "TYPESAFE_API_KEY=ts\n"), ("ts", None))

    def test_none(self):
        self.assertEqual(self.key({}), (None, None))


class AskWriterTest(unittest.TestCase):
    """With jev, a small Claude model (config ask_model) writes the line when
    the session needs the user and asks no open question."""

    SESS = {"name": "web", "status": "idle", "statusSince": 1, "cwd": "/x"}
    LAST = "I sent the points to the other session.\n\nOne step for you: reload the extension."

    def run_score(self, kind="action", last=LAST, status="idle", cfg=None, reply=None):
        import asyncio
        from unittest import mock
        from claude_focus import scorer
        jev_result = {"priority": 40, "kind": kind, "ask": "rules line", "cost_usd": 0.0001, "model": "jev"}
        cfg = dict(store.load_config(), scorer="jev", **(cfg or {}))
        data = {"structured_output": {"ask": "Reload the extension in chrome://extensions"},
                "total_cost_usd": 0.0005} if reply is None else reply
        with mock.patch.object(jev, "score", mock.AsyncMock(return_value=dict(jev_result))), \
                mock.patch.object(scorer, "_claude", mock.AsyncMock(return_value=data)) as call:
            r = asyncio.run(scorer.score(dict(self.SESS, status=status), "", last, cfg))
        return r, call

    def test_the_model_writes_the_line(self):
        r, call = self.run_score()
        self.assertEqual(r["ask"], "Reload the extension in chrome://extensions")
        self.assertEqual((r["ask_model"], r["ask_cost_usd"]), ("claude-haiku-5-5", 0.0005))
        prompt, system, _, model, _ = call.await_args.args
        self.assertEqual(model, "claude-haiku-5-5")
        self.assertIn("reload the extension", prompt)
        self.assertIn("immediate next step", system)

    def test_not_for_a_session_that_needs_nothing(self):
        for kind in ("info", "nothing"):
            r, call = self.run_score(kind=kind)
            call.assert_not_awaited()
            self.assertEqual(r["ask"], "rules line")

    def test_not_for_an_open_question(self):
        r, call = self.run_score(last="Done.\n\nOPEN QUESTIONS:\n- Which plan? [A / B]")
        call.assert_not_awaited()

    def test_off_in_the_config(self):
        r, call = self.run_score(cfg={"ask_model": ""})
        call.assert_not_awaited()
        self.assertNotIn("ask_model", r)

    def test_a_failed_call_keeps_the_rules_line(self):
        r, call = self.run_score(reply=None or {})
        self.assertEqual(r["ask"], "rules line")
        self.assertNotIn("ask_model", r)

    def test_a_long_line_is_cut(self):
        r, _ = self.run_score(reply={"structured_output": {"ask": "x" * 300}, "total_cost_usd": 0.0005})
        self.assertLessEqual(len(r["ask"]), ask.MAX)


if __name__ == "__main__":
    unittest.main()


class StateTextTest(unittest.TestCase):
    def test_both_scorers_learn_what_a_shell_session_is(self):
        self.assertEqual(scorer.state_text({"status": "waiting", "waitingFor": "permission"}), "waiting permission")
        shell = {"status": "shell", "cwd": "/x", "name": "n"}
        self.assertTrue(scorer.state_text(shell).startswith("shell (its turn has ended, but background shells"))
        prompt = scorer.build_prompt(shell, "", "", dt.datetime(2026, 10, 22, 11, 0))
        self.assertIn("STATE: shell (its turn has ended", prompt)
