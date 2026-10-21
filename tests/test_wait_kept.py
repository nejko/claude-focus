"""A wait ends only when the user types. A session lost its wait on
2026-10-14 at 21:05: Claude Code compacted the idle session by itself, the
session was busy for a minute, and the engine took that for a new turn."""
import asyncio
import datetime as dt
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402
from claude_focus import pending as pend  # noqa: E402
from claude_focus import sessions as sessions_mod  # noqa: E402
from claude_focus import store  # noqa: E402

ARMED = int(dt.datetime(2026, 10, 14, 20, 11, 56).timestamp() * 1000)  # the turn that set the wait ended
MIN = 60000
SESS = {"pid": 77664, "sessionId": "s1", "name": "crm-api-article", "cwd": "/x"}


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def prompt(ms, text, kind="human", **extra):
    return dict({"type": "user", "timestamp": iso(ms), "origin": {"kind": kind}, "message": {"content": text}}, **extra)


def system(ms, subtype, **extra):
    return dict({"type": "system", "subtype": subtype, "timestamp": iso(ms)}, **extra)


# The entries of that session's transcript, 2026-10-14 20:11:37 to 21:07:13 (local time)
HISTORY = [
    prompt(ARMED - 18000, "shall claude-focus move this to pending?"),
    {"type": "assistant", "timestamp": iso(ARMED - 500), "message": {"content": [{"type": "text", "text": "Yes."}]}},
    system(ARMED - 100, "turn_duration"),
    {"type": "last-prompt"},
    system(ARMED + 3 * MIN, "away_summary", content="We are testing the CRM's API and CLI"),
]
COMPACTION = [
    system(ARMED + 55 * MIN, "compact_boundary", content="Conversation compacted"),
    {"type": "user", "timestamp": iso(ARMED + 55 * MIN), "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
     "message": {"content": "This session is being continued from a previous conversation that ran out of context."}},
    {"type": "attachment", "timestamp": iso(ARMED + 55 * MIN)},
    system(ARMED + 55 * MIN, "informational", content="Compacted while idle, before the prompt cache expired"),
]


class WaitKeptTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        names = {"DIR": d, "STATE": os.path.join(d, "state.json"), "LOCK": os.path.join(d, ".lock"),
                 "LOG": os.path.join(d, "focus.log"), "PRIORITIES": os.path.join(d, "p.md")}
        for name, value in names.items():
            p = mock.patch.object(store, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.transcript = os.path.join(d, "s1.jsonl")
        p = mock.patch.object(sessions_mod, "transcript_path", return_value=self.transcript)
        p.start()
        self.addCleanup(p.stop)
        self.entries = list(HISTORY)
        self.app = appmod.App(None)
        self.app._user_in = lambda s: False
        self.app.fire = mock.AsyncMock()
        pend.set_pending(SESS, int(time.time() * 1000) + 86400000, None, 5, "night 3 runs")
        self.tick("idle", ARMED)  # armed

    def tick(self, status, since):
        with open(self.transcript, "w") as f:
            f.write("\n".join(json.dumps(e) for e in self.entries) + "\n")
        self.app.sessions = [dict(SESS, key="s1", status=status, statusSince=since)]
        asyncio.run(self.app.pending_tick(store.load_state()))
        return store.load_state().get("pending", {}).get("s1")

    def test_idle_compaction_keeps_the_wait(self):
        self.entries += COMPACTION
        self.assertEqual(self.tick("busy", ARMED + 54 * MIN)["armed_stamp"], ARMED)
        rec = self.tick("idle", ARMED + 55 * MIN)
        self.assertEqual(rec["armed_stamp"], ARMED + 55 * MIN)  # the next change counts from here
        self.entries.append(prompt(ARMED + 70 * MIN, "is this session still doing something?"))
        self.assertIsNone(self.tick("busy", ARMED + 70 * MIN))

    def test_no_wake_up_while_it_compacts(self):
        store.update_state(lambda st: st["pending"]["s1"].__setitem__("until", ARMED + 54 * MIN))
        self.entries += COMPACTION
        self.tick("busy", ARMED + 54 * MIN)
        self.app.fire.assert_not_called()
        self.tick("idle", ARMED + 55 * MIN)
        self.app.fire.assert_called_once()

    def test_background_task_and_peer_message_keep_the_wait(self):
        self.entries.append(prompt(ARMED + 5 * MIN, "<task-notification>\n<task-id>bll1so8z4</task-id>",
                                   "task-notification"))
        self.assertIsNotNone(self.tick("busy", ARMED + 5 * MIN))
        self.entries.append(prompt(ARMED + 6 * MIN, "Another Claude session sent a message:", "peer", isMeta=True))
        self.assertIsNotNone(self.tick("busy", ARMED + 6 * MIN))
        self.assertIsNotNone(self.tick("idle", ARMED + 7 * MIN))

    def test_answer_to_a_question_keeps_the_wait(self):
        # The session set the wait and then asked a question in the same turn:
        # the wait was armed while the dialog was open.
        self.entries.append({"type": "user", "timestamp": iso(ARMED + 2 * MIN), "message": {
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Fix it here now"}]}})
        self.assertIsNotNone(self.tick("busy", ARMED + 2 * MIN))

    def test_his_prompt_ends_the_wait_also_when_it_is_written_late(self):
        self.assertIsNotNone(self.tick("busy", ARMED + 10 * MIN))  # the transcript has no prompt yet
        self.entries.append(prompt(ARMED + 10 * MIN, "go on"))
        self.assertIsNone(self.tick("idle", ARMED + 12 * MIN))


class TypedSinceTest(unittest.TestCase):
    def since(self, entries, ms=ARMED):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(e) for e in entries))
        with mock.patch.object(sessions_mod, "transcript_path", return_value=f.name):
            return sessions_mod.user_typed_since("/x", "s1", ms)

    def test_the_prompt_of_the_turn_that_set_the_wait_is_older(self):
        self.assertFalse(self.since(HISTORY + COMPACTION))

    def test_a_wake_up_from_claude_focus_is_not_his(self):
        self.assertFalse(self.since(HISTORY + [prompt(ARMED + MIN, "[claude-focus wake-up] The time you set has come")]))

    def test_his_prompt_after_the_wait(self):
        self.assertTrue(self.since(HISTORY + COMPACTION + [prompt(ARMED + 70 * MIN, "hi")]))


class LastUserAnswerTest(unittest.TestCase):
    def answer(self, entries):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(e) for e in entries))
        with mock.patch.object(sessions_mod, "transcript_path", return_value=f.name):
            return sessions_mod.last_user_answer("/x", "s1")

    def notification_turn(self, ms):
        return [prompt(ms, "<task-notification>\n<task-id>bll1so8z4</task-id>", "task-notification"),
                {"type": "assistant", "timestamp": iso(ms + 1000), "message": {"content": [{"type": "text", "text": "ok"}]}}]

    def test_answer_turn_ended_before_the_turns_of_background_tasks(self):
        # The same session, 2026-10-15: the user's question at 08:57, the
        # answer ended 09:00:53, Monitor events started turns after it
        entries = HISTORY + COMPACTION + self.notification_turn(ARMED + 60 * MIN)
        self.assertEqual(self.answer(entries), ARMED - 100)

    def test_his_message_in_the_running_turn(self):
        self.assertEqual(self.answer(HISTORY[:2]), "running")

    def test_compaction_summary_and_wake_ups_are_not_his(self):
        entries = [system(ARMED, "turn_duration")] + COMPACTION + [prompt(ARMED + 60 * MIN, "[claude-focus wake-up] x")]
        self.assertIsNone(self.answer(entries))


if __name__ == "__main__":
    unittest.main()
