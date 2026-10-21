import json
import tempfile
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import sessions  # noqa: E402


def entry(kind, content):
    return json.dumps({"type": kind, "message": {"content": content}})


class LastTextTest(unittest.TestCase):
    def run_lines(self, lines):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines))
        with mock.patch.object(sessions, "transcript_path", return_value=f.name):
            return sessions.last_assistant_text("/x", "id")

    def ask(self, tid):
        return entry("assistant", [{"type": "tool_use", "id": tid, "name": "AskUserQuestion",
                                    "input": {"questions": [{"question": "Send it?", "options": [{"label": "Yes"}]}]}}])

    def test_open_question_is_listed(self):
        out = self.run_lines([entry("assistant", [{"type": "text", "text": "Report."}]), self.ask("t1")])
        self.assertIn("OPEN QUESTIONS:\n- Send it? [Yes]", out)

    def test_question_answered_in_dialog_is_closed(self):
        out = self.run_lines([self.ask("t1"),
                              entry("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "Yes"}]),
                              entry("assistant", [{"type": "text", "text": "Sent."}])])
        self.assertEqual(out, "Sent.")

    def test_short_last_reply_falls_back_to_last_report(self):
        report = "Report.\n\n## Still open\n\n- You: ship the return by 6 October."
        out = self.run_lines([entry("assistant", [{"type": "text", "text": report}]),
                              entry("user", "renamed"),
                              entry("assistant", [{"type": "text", "text": "No response requested."}])])
        self.assertTrue(out.startswith(report))
        self.assertTrue(out.endswith("(Latest reply: No response requested.)"))

    def test_other_tool_result_keeps_question_open(self):
        out = self.run_lines([self.ask("t1"),
                              entry("user", [{"type": "tool_result", "tool_use_id": "t9", "content": "x"}])])
        self.assertIn("OPEN QUESTIONS", out)


class LastQuestionTest(unittest.TestCase):
    def outcome(self, lines):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines))
        with mock.patch.object(sessions, "transcript_path", return_value=f.name):
            return sessions.last_question("/x", "id")

    def ask(self, tid):
        return entry("assistant", [{"type": "tool_use", "id": tid, "name": "AskUserQuestion", "input": {}}])

    def result(self, tid, is_error=None):
        r = {"type": "tool_result", "tool_use_id": tid, "content": "x"}
        if is_error is not None:
            r["is_error"] = is_error
        return entry("user", [r])

    def test_submitted_answers(self):
        self.assertEqual(self.outcome([self.ask("t1"), self.result("t1")]), ("t1", "answered"))

    def test_esc_or_chat_about_this_is_declined(self):
        self.assertEqual(self.outcome([self.ask("t1"), self.result("t1", True)]), ("t1", "declined"))

    def test_the_last_question_counts(self):
        out = self.outcome([self.ask("t1"), self.result("t1"), self.ask("t2"), self.result("t2", True)])
        self.assertEqual(out, ("t2", "declined"))

    def test_no_question(self):
        self.assertEqual(self.outcome([entry("assistant", [{"type": "text", "text": "Hi."}])]), (None, None))


class TurnHasUserMessageTest(unittest.TestCase):
    """Entry shapes as Claude Code 2.1 writes them (seen 2026-10-14)."""

    def has(self, lines):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(e) for e in lines))
        with mock.patch.object(sessions, "transcript_path", return_value=f.name):
            return sessions.turn_has_user_message("/x", "id")

    def prompt(self, text, kind="human"):
        return {"type": "user", "origin": {"kind": kind}, "message": {"content": text}}

    END = {"type": "system", "subtype": "turn_duration"}
    WORK = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t", "name": "Bash"}]}}
    RESULT = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t"}]}}

    def test_turn_that_the_user_started(self):
        # A session on 2026-10-14 06:26: the user's question, then work, then the wait
        self.assertTrue(self.has([self.END, self.prompt("why did you miss it?"), self.WORK, self.RESULT]))

    def test_the_users_message_is_from_an_earlier_turn(self):
        self.assertFalse(self.has([self.prompt("why?"), self.WORK, self.END]))

    def test_task_notification_or_peer_message_starts_the_turn(self):
        self.assertFalse(self.has([self.END, self.prompt("<task-notification>...", "task-notification")]))
        self.assertFalse(self.has([self.END, self.prompt("Another Claude session sent a message", "peer")]))

    def test_task_notification_later_in_the_users_turn(self):
        self.assertTrue(self.has([self.END, self.prompt("check it"), self.WORK, self.RESULT,
                                  self.prompt("<task-notification>...", "task-notification")]))

    def test_claude_focus_messages_are_not_the_users(self):
        self.assertFalse(self.has([self.END, self.prompt("[claude-focus wake-up] Your check succeeded.")]))
        self.assertFalse(self.has([self.END, self.prompt('<pasted_content id="b0bc"> [claude-focus wake-up] x')]))
        self.assertFalse(self.has([self.END, self.prompt("[claude-focus restore] The user restarted the computer")]))

    def test_prompt_with_screenshots_is_one_long_line(self):
        # claude-focus-74, 2026-10-14 06:40: "why [Image #5] [Image #6]" was a
        # line of several MB, and a fixed tail cut it.
        shot = {"type": "image", "source": {"type": "base64", "data": "A" * 5000}}
        big = {"type": "user", "origin": {"kind": "human"},
               "message": {"content": [{"type": "text", "text": "why [Image #5]"}, shot]}}
        quoted = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": 'a line with "subtype":"turn_duration" in it'}]}}
        lines = [self.prompt("old"), self.END, big, quoted, self.WORK, self.RESULT]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(e) for e in lines))
        for chunk in (64, 1000, 1 << 20):
            turn = sessions.current_turn(f.name, chunk=chunk)
            self.assertEqual([json.loads(x)["type"] for x in turn], ["user", "assistant", "assistant", "user"], chunk)
        self.assertTrue(self.has(lines))

    def test_no_turn_end_in_the_file(self):
        self.assertTrue(self.has([self.prompt("first message"), self.WORK]))

    def test_without_origin_field(self):
        self.assertTrue(self.has([self.END, {"type": "user", "message": {"content": "hi"}}]))
        self.assertFalse(self.has([self.END, {"type": "user", "message": {"content": "<command-name>/exit"}}]))


class ListSessionsTest(unittest.TestCase):
    def test_headless_runs_are_skipped(self):
        rows = [{"pid": 1, "sessionId": "a", "status": "busy", "entrypoint": "cli", "statusUpdatedAt": 1},
                {"pid": 2, "sessionId": "b", "status": "busy", "entrypoint": "sdk-cli", "statusUpdatedAt": 1},
                {"pid": 3, "sessionId": "c", "status": "busy", "entrypoint": "sdk-py", "statusUpdatedAt": 1},
                {"pid": 4, "sessionId": "d", "status": "busy", "statusUpdatedAt": 1}]
        with mock.patch.object(sessions, "_from_files", return_value=rows):
            live = sessions.list_sessions()
        self.assertEqual([r["sessionId"] for r in live], ["a", "d"])


if __name__ == "__main__":
    unittest.main()
