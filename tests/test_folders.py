import json
import os
import shutil
import tempfile
import time
import unittest

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import folders  # noqa: E402

DAY = 86400


class CandidatesTest(unittest.TestCase):
    """The folders offered for a new session: Claude Code's own list and the
    subfolders of the project folders, newest first."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.mkdir("Projects")
        self.transcripts = self.mkdir("transcripts")
        self.claude_json = os.path.join(self.tmp, "claude.json")

    def mkdir(self, *parts, age_days=None):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(path, exist_ok=True)
        if age_days is not None:
            t = time.time() - age_days * DAY
            os.utime(path, (t, t))
        return path

    def session(self, path, age_days):
        """A transcript of a session in path, written age_days ago."""
        d = os.path.join(self.transcripts, folders._encoded(path))
        os.makedirs(d, exist_ok=True)
        f = os.path.join(d, "s.jsonl")
        open(f, "w").close()
        t = time.time() - age_days * DAY
        os.utime(f, (t, t))

    def candidates(self, known=()):
        with open(self.claude_json, "w") as f:
            json.dump({"projects": {p: {} for p in known}}, f)
        return folders.candidates([self.root], self.claude_json, self.transcripts, skip=("/scratch/",))

    def test_newest_first_from_both_sources(self):
        old = self.mkdir("Projects", "old-app", age_days=30)
        used = self.mkdir("Projects", "api")
        elsewhere = self.mkdir("elsewhere", "site")
        self.session(used, 1)
        self.session(elsewhere, 0.1)
        out = self.candidates([elsewhere])
        self.assertEqual([p for p, _, _ in out], [elsewhere, used, old])
        self.assertEqual([u for _, _, u in out], [True, True, False])

    def test_gone_temporary_and_hidden_folders_are_left_out(self):
        self.mkdir("Projects", ".cache")
        open(os.path.join(self.root, "notes.txt"), "w").close()
        gone = os.path.join(self.tmp, "gone")
        out = self.candidates([gone, "/scratch/run-1"])
        self.assertEqual(out, [])

    def test_a_folder_without_transcripts_counts_by_its_own_time(self):
        # Claude Code deletes old transcripts; the folder stays in its list.
        a = self.mkdir("a", age_days=2)
        out = self.candidates([a])
        self.assertEqual([(p, u) for p, _, u in out], [(a, False)])
        self.assertAlmostEqual(out[0][1], time.time() - 2 * DAY, delta=60)

    def test_one_row_per_folder(self):
        # On macOS ~/projects is ~/Projects; a link is its target. The first spelling wins.
        real = self.mkdir("Projects", "api")
        link = os.path.join(self.tmp, "api-link")
        os.symlink(real, link)
        out = self.candidates([link, real])
        self.assertEqual([p for p, _, _ in out], [link])

    def test_no_claude_json(self):
        self.mkdir("Projects", "x")
        out = folders.candidates([self.root], os.path.join(self.tmp, "none.json"), self.transcripts)
        self.assertEqual([os.path.basename(p) for p, _, _ in out], ["x"])


class SearchTest(unittest.TestCase):
    FOLDERS = [(p, 0, True) for p in ("~/Projects/sellestial-iso", "~/Projects/claude-focus",
                                      "~/Projects/focus-notes", "~/Projects/ultima", "~/clients/claude-docs")]

    def names(self, query):
        return [os.path.basename(p) for p, _, _ in folders.search(self.FOLDERS, query)]

    def test_empty_query_keeps_the_order(self):
        self.assertEqual(self.names(""), [os.path.basename(p) for p, _, _ in self.FOLDERS])

    def test_name_start_then_name_then_path(self):
        self.assertEqual(self.names("focus"), ["focus-notes", "claude-focus"])
        self.assertEqual(self.names("projects ult"), ["ultima"])  # every word, anywhere in the path
        self.assertEqual(self.names("clients"), ["claude-docs"])

    def test_letters_in_order_find_an_abbreviation(self):
        self.assertEqual(self.names("clfo"), ["claude-focus"])
        self.assertEqual(self.names("zzz"), [])

    def test_abbreviations_only_when_no_path_has_the_text(self):
        # "ult" is in ultima; sellestial-iso has u, l, t in order only across the path.
        self.assertEqual(self.names("ult"), ["ultima"])
        self.assertEqual(self.names("prjfo"), [])  # the letters must be in the folder's own name

    def test_case_does_not_matter(self):
        self.assertEqual(self.names("ULTIMA"), ["ultima"])

    def test_marks(self):
        self.assertEqual(folders.marks("~/x/claude-focus", "focus"), set(range(11, 16)))
        self.assertEqual(folders.marks("~/x/claude-focus", "clfo"), {4, 5, 11, 12})
        self.assertEqual(folders.marks("~/x/ultima", ""), set())


class NewFolderTest(unittest.TestCase):
    """The folder that the picker offers to create from what the user typed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "Projects")
        os.makedirs(os.path.join(self.root, "api"))
        self.roots = [os.path.join(self.tmp, "missing"), self.root]

    def test_a_name_goes_into_the_first_project_folder_that_exists(self):
        self.assertEqual(folders.new_folder(" bine-app ", self.roots), os.path.join(self.root, "bine-app"))
        self.assertEqual(folders.new_folder("clients/acme", self.roots), os.path.join(self.root, "clients", "acme"))

    def test_a_path_as_typed(self):
        self.assertEqual(folders.new_folder(self.tmp + "/notes/", self.roots), os.path.join(self.tmp, "notes"))
        self.assertEqual(folders.new_folder("~/x-new-folder", self.roots), os.path.expanduser("~/x-new-folder"))

    def test_without_a_project_folder_the_home_folder(self):
        self.assertEqual(folders.new_folder("x", [self.roots[0]]), os.path.join(os.path.expanduser("~"), "x"))

    def test_nothing_typed_or_the_folder_exists(self):
        self.assertIsNone(folders.new_folder("  ", self.roots))
        self.assertIsNone(folders.new_folder("api", self.roots))
        self.assertIsNone(folders.new_folder(self.root, self.roots))


if __name__ == "__main__":
    unittest.main()
