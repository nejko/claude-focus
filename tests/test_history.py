"""The search over past sessions: what is indexed from a transcript, how a
transcript that grows is read on, and what a search finds first. The picker
resumes a session it finds, and x closes a session with /exit."""
import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402
from claude_focus import history  # noqa: E402

SID = "11111111-2222-3333-4444-555555555555"
OTHER = "66666666-7777-8888-9999-000000000000"


def entry(kind, content=None, **extra):
    e = {"type": kind, "cwd": "/w/acme", "entrypoint": "cli", "sessionId": SID,
         "timestamp": "2026-11-09T10:00:00.000Z"}
    if content is not None:
        e["message"] = {"role": kind, "content": content}
    e.update(extra)
    return e


TRANSCRIPT = [
    entry("user", "Pripravi račun za Acme za oktober"),
    entry("assistant", [{"type": "thinking", "thinking": "secret plan"},
                        {"type": "text", "text": "Račun je pripravljen v drafts."},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "ls toolinput"}}]),
    entry("user", [{"type": "tool_result", "content": "tooloutput lines"}]),
    entry("user", "<command-name>/model</command-name>"),
    entry("user", "meta note", isMeta=True),
    entry("assistant", [{"type": "text", "text": "subagent words"}], isSidechain=True),
    {"type": "ai-title", "aiTitle": "October invoice for Acme", "sessionId": SID},
    {"type": "last-prompt", "lastPrompt": "Pripravi račun", "sessionId": SID},
]


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.db = os.path.join(tempfile.mkdtemp(), "history.db")
        self.dir = os.path.join(self.root, "-w-acme")
        os.makedirs(self.dir)
        self.path = os.path.join(self.dir, SID + ".jsonl")
        self.write(TRANSCRIPT)

    def write(self, entries, mode="w"):
        with open(self.path, mode) as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def sync(self):
        return history.sync(self.root, self.db)

    def search(self, query):
        return history.search(query, self.db)

    def test_what_is_indexed(self):
        self.sync()
        con = history.connect(self.db)
        bodies = [r[0] for r in con.execute("SELECT body FROM texts")]
        self.assertEqual(bodies, ["Pripravi račun za Acme za oktober", "Račun je pripravljen v drafts."])
        row = con.execute("SELECT cwd, entrypoint, title, prompt FROM files").fetchone()
        self.assertEqual(row, ("/w/acme", "cli", "October invoice for Acme", "Pripravi račun"))

    def test_words_without_diacritics_find_it(self):
        self.sync()
        [hit] = self.search("racun drafts")
        self.assertEqual((hit["session"], hit["cwd"], hit["label"]), (SID, "/w/acme", "October invoice for Acme"))
        self.assertIn("\x01drafts\x02", hit["snippet"])
        self.assertEqual(self.search("tooloutput"), [])
        self.assertEqual(self.search("toolinput"), [])
        self.assertEqual(self.search("secret"), [])
        self.assertEqual(self.search("subagent"), [])

    def test_a_growing_transcript_is_read_on(self):
        self.sync()
        self.write([entry("user", "dodaj še popust"), {"type": "custom-title", "customTitle": "acme-racuni"}], "a")
        self.assertEqual(self.sync(), 1)
        [hit] = self.search("popust")
        self.assertEqual(hit["label"], "acme-racuni · October invoice for Acme")  # the user's name, then the title
        self.assertEqual(self.sync(), 0)  # nothing new: the file is not read again
        con = history.connect(self.db)
        self.assertEqual(con.execute("SELECT count(*) FROM texts").fetchone()[0], 3)

    def test_a_line_still_being_written_waits(self):
        with open(self.path, "a") as f:
            f.write(json.dumps(entry("user", "half written")))  # no newline yet
        self.sync()
        self.assertEqual(self.search("half"), [])
        with open(self.path, "a") as f:
            f.write("\n")
        self.sync()
        self.assertEqual(len(self.search("half")), 1)

    def test_a_replaced_transcript_is_read_again(self):
        self.sync()
        os.remove(self.path)
        self.write([entry("user", "povsem nova vsebina")])
        self.sync()
        self.assertEqual(self.search("oktober"), [])
        self.assertEqual(len(self.search("nova vsebina")), 1)

    def test_a_deleted_transcript_leaves_the_index(self):
        self.sync()
        os.remove(self.path)
        self.sync()
        self.assertEqual(self.search("racun"), [])

    def test_headless_and_desktop_sessions_are_left_out(self):
        for ep in ("sdk-cli", "claude-desktop"):
            self.write([entry("user", "skrita vsebina", entrypoint=ep)])
            self.sync()
            self.assertEqual(self.search("skrita"), [], ep)

    def test_name_and_title_matches_come_first(self):
        other = os.path.join(self.dir, OTHER + ".jsonl")
        with open(other, "w") as f:
            f.write(json.dumps(entry("user", "Acme newsletter draft", sessionId=OTHER,
                                     timestamp="2026-11-09T12:00:00.000Z")) + "\n")
        self.sync()
        hits = self.search("invoice")  # only the title of the first session has it
        self.assertEqual([h["session"] for h in hits], [SID])
        hits = self.search("acme")  # both: the folder name matches both
        self.assertEqual([h["session"] for h in hits], [OTHER, SID])  # newest first
        self.assertEqual({h["group"] for h in hits}, {"named"})
        [hit] = self.search("drafts")  # only in what Claude wrote
        self.assertEqual(hit["group"], "talk")

    def test_an_empty_session_is_left_out(self):
        # Opened and closed at once: only Claude Code's own command echo, nothing to resume.
        self.write([entry("user", "<command-name>/exit</command-name>")])
        self.sync()
        self.assertEqual(self.search("acme"), [])

    def test_without_a_title_the_first_message(self):
        self.write([entry("user", "\n  Preveri izvoz kontaktov\nza Acme"), entry("user", "in še to")])
        self.sync()
        [hit] = self.search("acme")
        self.assertEqual(hit["label"], "Preveri izvoz kontaktov")

    def test_labels(self):
        self.assertEqual(history.label("linkmcp", "Website redesign", None), "linkmcp · Website redesign")
        self.assertEqual(history.label("Acme", "Acme invoices", None), "Acme invoices")  # the name is in it
        self.assertEqual(history.label(None, "Title", "first", "Opis seje"), "Opis seje")  # a description wins
        self.assertEqual(history.label("x", None, None), "x")
        self.assertEqual(history.label(None, None, None), "(no title)")

    def test_timestamps_are_utc(self):
        self.assertEqual(history._when("2026-07-01T12:00:00.000Z"), 1782907200)  # in summer time too

    def test_a_query_fts_cannot_parse(self):
        self.sync()
        self.assertEqual(self.search('"" * ( ^'), [])

    def test_one_run_at_a_time(self):
        import fcntl
        with open(self.db + ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.sync(), 0)  # another run holds the lock: nothing read
        self.assertEqual(self.sync(), 1)

    def test_no_index_yet(self):
        self.assertEqual(history.search("acme", os.path.join(self.root, "none.db")), [])


class DescribeTest(unittest.TestCase):
    """A small model writes one line per session once it has rested; it
    stops at the first failed call."""
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.db = os.path.join(tempfile.mkdtemp(), "history.db")
        os.makedirs(os.path.join(self.root, "-w-acme"))
        for sid in (SID, OTHER):
            with open(os.path.join(self.root, "-w-acme", sid + ".jsonl"), "w") as f:
                for e in TRANSCRIPT:
                    f.write(json.dumps(dict(e, sessionId=sid)) + "\n")
        history.sync(self.root, self.db)
        self.later = history._when("2026-11-09T10:00:00.000Z") + history.QUIET_S + 1

    def describe(self, replies, now=None):
        call = mock.AsyncMock(side_effect=replies)
        with mock.patch.object(appmod.scorer, "_claude", call):
            n = history.describe(self.db, "haiku", now or self.later)
        return n, call

    def test_written_and_shown(self):
        n, call = self.describe([{"structured_output": {"about": "Oktobrski račun za Acme, čaka na pošiljanje"},
                                  "total_cost_usd": 0.0004}] * 2)
        self.assertEqual(n, 2)
        prompt = call.await_args_list[0].args[0]
        self.assertIn("Pripravi račun za Acme", prompt)  # the first message
        self.assertIn("CLAUDE: Račun je pripravljen v drafts.", prompt)  # the last messages, with roles
        [hit, _] = history.search("racun", self.db)
        self.assertEqual(hit["label"], "Oktobrski račun za Acme, čaka na pošiljanje")
        self.assertEqual(self.describe([])[0], 0)  # nothing more until REDESCRIBE_N new messages

    def test_not_while_it_works(self):
        n, call = self.describe([], now=self.later - 2)
        self.assertEqual(n, 0)
        call.assert_not_awaited()

    def test_a_failed_call_stops_the_run(self):
        n, _ = self.describe([None, None])
        self.assertEqual(n, 0)
        con = history.connect(self.db)
        self.assertEqual(con.execute("SELECT count(*) FROM files WHERE about IS NULL").fetchone()[0], 2)

    def test_off_without_a_model(self):
        with mock.patch.object(appmod.scorer, "_claude", mock.AsyncMock()) as call:
            self.assertEqual(history.describe(self.db, "", self.later), 0)
        call.assert_not_awaited()


class PickerTest(unittest.TestCase):
    def setUp(self):
        self.app = appmod.App(None)
        self.app.bridge = mock.Mock(go=mock.AsyncMock(return_value=True), pane_of=lambda s: s.get("pane"),
                                    label="iTerm2")
        self.app.pick = {"term": {"pane": "p9", "tty": "ttys009"}, "sel": 0}
        self.app.matches = [("/w/acme", 0, True)]
        self.app.new_folder = None
        self.home = tempfile.mkdtemp()
        self.app.hits = [{"session": SID, "cwd": self.home, "label": "x", "last_at": 0, "snippet": None}]

    def test_the_rows_after_the_folders_are_sessions(self):
        self.assertIsNone(self.app._hit())
        self.app.pick["sel"] = 1
        self.assertEqual(self.app._hit()["session"], SID)

    def test_without_a_folder_the_first_session_is_selected(self):
        # Words from a session match no folder: Enter must resume it, not make a folder of the words.
        self.app.mode, self.app.matches, self.app.new_folder, self.app.hits = "folders", [], "/w/acme invoice", []
        with mock.patch.object(appmod.history, "search", return_value=[{"session": SID}]):
            asyncio.run(self.app._search_sessions("acme invoice", self.app.hit_seq))
        self.assertEqual(self.app._hit(), {"session": SID})
        self.app.matches, self.app.pick["sel"] = [("/w/acme", 0, True)], 0  # a folder matches: it stays selected
        with mock.patch.object(appmod.history, "search", return_value=[{"session": SID}]):
            asyncio.run(self.app._search_sessions("acme", self.app.hit_seq))
        self.assertEqual(self.app.pick["sel"], 0)

    def test_a_stale_search_is_dropped(self):
        self.app.mode, self.app.hits = "folders", []
        with mock.patch.object(appmod.history, "search", return_value=[{"session": SID}]):
            asyncio.run(self.app._search_sessions("old", self.app.hit_seq - 1))
        self.assertEqual(self.app.hits, [])

    def test_two_groups_with_headings_and_more(self):
        import re
        self.app.pal = appmod.look.DARK
        hit = lambda n, group: {"session": f"s{n}", "cwd": "/w/x", "label": f"label {n}", "last": "",
                                "last_at": 0, "snippet": None, "group": group}
        self.app.hits = [hit(n, "named") for n in range(3)] + [hit(n, "talk") for n in range(3, 9)]
        self.app.pick["sel"] = 1 + 7  # after the one folder: the eighth session, in the second group
        text = [re.sub(r"\x1b\[[0-9;]*m", "", x) for x in self.app._hit_lines(120, 8)]
        self.assertIn("PAST SESSIONS  9", text[0])
        self.assertTrue(text[1].strip().startswith("↑ "))
        self.assertIn("found in the conversation 6", "".join(text))
        self.assertTrue(any("label 7" in x and x.startswith("▌") for x in text))  # the selection is in view
        self.assertEqual(text[-1].strip(), "↓ 1 more")
        self.app.pick["sel"] = 1
        text = [re.sub(r"\x1b\[[0-9;]*m", "", x) for x in self.app._hit_lines(120, 30)]
        self.assertIn("in the name, description or folder 3", text[1])
        self.assertNotIn("more", "".join(text))

    def test_enter_resumes_it_in_the_free_terminal(self):
        with mock.patch.object(self.app, "start_session", mock.AsyncMock()) as start:
            asyncio.run(self.app.resume_hit(self.app.pick["term"], self.app.hits[0]))
        start.assert_awaited_once_with(self.app.pick["term"], self.home, flags=f" --resume {SID}")

    def test_a_running_session_is_not_started_twice(self):
        self.app.sessions = [{"sessionId": SID, "pane": "p3", "name": "x"}]
        with mock.patch.object(self.app, "start_session", mock.AsyncMock()) as start:
            asyncio.run(self.app.resume_hit(self.app.pick["term"], self.app.hits[0]))
        start.assert_not_awaited()
        self.app.bridge.go.assert_awaited_once()


class CloseTest(unittest.TestCase):
    def setUp(self):
        self.app = appmod.App(None)
        self.app.bridge = mock.Mock(send_prompt=mock.AsyncMock(return_value=(True, "")),
                                    pane_of=lambda s: s.get("pane"), label="iTerm2")

    def test_only_a_session_at_rest(self):
        for status, why in (("busy", "working"), ("waiting", "question"), ("shell", "background shells")):
            self.app.ask_close({"s": {"name": "x", "status": status, "pane": "p1"}})
            self.assertEqual(self.app.mode, "main")
            self.assertIn(why, self.app.msg)
        self.app.ask_close({"s": {"name": "x", "status": "idle"}})
        self.assertIn("no iTerm2 pane", self.app.msg)
        self.app.ask_close({"s": {"name": "x", "status": "idle", "pane": "p1"}})
        self.assertEqual(self.app.mode, "close")

    def test_y_types_exit(self):
        s = {"name": "x", "status": "idle", "pane": "p1"}
        self.app.ask_close({"s": s})
        asyncio.run(self.app.on_key("y"))
        self.app.bridge.send_prompt.assert_awaited_once_with("p1", "/exit")
        self.assertEqual(self.app.mode, "main")

    def test_esc_keeps_it(self):
        self.app.ask_close({"s": {"name": "x", "status": "idle", "pane": "p1"}})
        asyncio.run(self.app.on_key("\x1b"))
        self.app.bridge.send_prompt.assert_not_awaited()
        self.assertEqual(self.app.mode, "main")


if __name__ == "__main__":
    unittest.main()
