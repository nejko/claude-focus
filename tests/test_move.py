import unittest

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import app as appmod  # noqa: E402


def item(name):
    return {"s": {"sessionId": name, "key": name, "name": name}}


class MoveTest(unittest.TestCase):
    def setUp(self):
        self.app = appmod.App(None)

    def view(self, need=(), working=()):
        self.app.view = {"need": [item(n) for n in need], "working": [item(n) for n in working]}

    def keys(self, *deltas):
        out = []
        for d in deltas:
            self.app.move(d)
            out.append(self.app.sel)
        return out

    def test_down_from_the_last_row_goes_to_the_top(self):
        self.view(need=["a", "b"], working=["c"])  # the highlight starts on "a", the top of NEED YOU
        self.assertEqual(self.keys(1, 1, 1, 1), ["b", "c", "a", "b"])

    def test_up_from_the_top_goes_to_the_last_row(self):
        self.view(need=["a", "b"], working=["c"])
        self.assertEqual(self.keys(-1, -1, -1, -1), ["c", "b", "a", "c"])

    def test_nothing_selected_and_no_top_row(self):
        self.view(working=["c", "d"])
        self.assertEqual(self.keys(1), ["c"])
        self.app.sel = None
        self.assertEqual(self.keys(-1), ["d"])

    def test_one_row(self):
        self.view(need=["a"])
        self.assertEqual(self.keys(1, -1), ["a", "a"])


if __name__ == "__main__":
    unittest.main()
