import datetime as dt
import unittest

import _env  # noqa: E402,F401  (first: a scratch CLAUDE_FOCUS_DIR)

from claude_focus import queue as q  # noqa: E402
from claude_focus.store import DEFAULT_CONFIG  # noqa: E402

NOW = dt.datetime(2026, 9, 30, 14, 0)
H = 3.6e6


def sess(sid, status="waiting", waited_h=1.0):
    return {"sessionId": sid, "key": sid, "name": sid, "status": status, "cwd": "/x",
            "statusSince": NOW.timestamp() * 1000 - waited_h * H}


def score(stamp, priority=50, **kw):
    d = {"priority": priority, "kind": "decision", "ask": "a", "deadline": "",
         "pending_until": "", "scored_for": stamp}
    d.update(kw)
    return d


class ClassifyTest(unittest.TestCase):
    def classify(self, sessions, entries):
        return q.classify(sessions, {"sessions": entries}, NOW, DEFAULT_CONFIG)

    def test_buckets_and_order(self):
        a, b, c = sess("a", waited_h=2), sess("b", waited_h=10), sess("c", status="busy")
        v = self.classify([a, b, c], {
            "a": {"score": score(a["statusSince"], 80)},
            "b": {"score": score(b["statusSince"], 70)},
        })
        self.assertEqual([i["s"]["sessionId"] for i in v["need"]], ["a", "b"])
        self.assertEqual([i["s"]["sessionId"] for i in v["working"]], ["c"])
        # aging: 0.15 point per hour
        self.assertAlmostEqual(v["need"][1]["eff"], 71.5)

    def test_shell_status_is_working(self):
        a, b = sess("a", status="shell"), sess("b", status="somethingnew")
        v = self.classify([a, b], {})
        self.assertEqual(len(v["working"]), 2)
        self.assertEqual(v["need"], [])

    def test_a_shell_session_that_asks_the_user_needs_them(self):
        # zupnijska-stran, 2026-10-22: its turn ended with "pick a design on the page"
        # while three background shells served the page; it sat under WORKING for
        # an hour and more, as "shells".
        asks, waits, unscored, done, later = (sess(n, status="shell")
                                              for n in ("asks", "waits", "unscored", "done", "later"))
        until = (NOW + dt.timedelta(hours=1)).timestamp() * 1000
        v = self.classify([asks, waits, unscored, done, later], {
            "asks": {"score": score(asks["statusSince"], 60)},
            "waits": {"score": score(waits["statusSince"], 10, kind="nothing")},
            "done": {"score": score(done["statusSince"], 60), "dismissed_stamp": done["statusSince"]},
            "later": {"score": score(later["statusSince"], 60), "snooze_until": until,
                      "snooze_stamp": later["statusSince"]},
        })
        self.assertEqual([i["s"]["sessionId"] for i in v["need"]], ["asks"])
        self.assertEqual([i["s"]["sessionId"] for i in v["pending"]], ["later"])
        # It waits for its own work, it is not scored yet, or the user marked it
        # done: working, and the shells wake it when they end.
        self.assertEqual(sorted(i["s"]["sessionId"] for i in v["working"]), ["done", "unscored", "waits"])

    def test_a_report_leaves_need_you_once_the_user_has_seen_it(self):
        # 2026-10-22: Nejc read a session's answer, and it stayed under NEED YOU.
        # A report needs nothing more once seen; an action does: seeing is not doing.
        read, unread, act, bg = (sess(n, status="shell" if n == "bg" else "idle")
                                 for n in ("read", "unread", "act", "bg"))
        kinds = {"read": "info", "unread": "info", "act": "action", "bg": "info"}
        state = {"sessions": {x["key"]: {"score": score(x["statusSince"], 30, kind=kinds[x["key"]])}
                              for x in (read, unread, act, bg)},
                 "seen": {"read": read["statusSince"] + 60000, "unread": unread["statusSince"] - 60000,
                          "act": act["statusSince"] + 60000, "bg": bg["statusSince"] + 60000}}
        v = q.classify([read, unread, act, bg], state, NOW, DEFAULT_CONFIG)
        self.assertEqual(sorted(i["s"]["sessionId"] for i in v["need"]), ["act", "unread"])
        self.assertEqual([(i["s"]["sessionId"], i.get("seen")) for i in v["free"]], [("read", True)])
        self.assertEqual([i["s"]["sessionId"] for i in v["working"]], ["bg"])  # its shells still run

    def test_a_score_of_the_turn_before_the_shells_does_not_count(self):
        a = sess("a", status="shell")
        v = self.classify([a], {"a": {"score": score(a["statusSince"] - 5000, 90)}})
        self.assertEqual([i["s"]["sessionId"] for i in v["working"]], ["a"])

    def test_aging_lifts_a_neglected_session(self):
        old, new = sess("old", waited_h=200), sess("new", waited_h=0.1)
        v = self.classify([old, new], {
            "old": {"score": score(old["statusSince"], 60)},
            "new": {"score": score(new["statusSince"], 65)},
        })
        self.assertEqual(v["need"][0]["s"]["sessionId"], "old")  # 60 + 10 cap > 65

    def test_aging_never_beats_a_critical_item(self):
        old, crit = sess("old", waited_h=500), sess("crit", waited_h=0)
        v = self.classify([old, crit], {
            "old": {"score": score(old["statusSince"], 75)},
            "crit": {"score": score(crit["statusSince"], 93)},
        })
        self.assertEqual(v["need"][0]["s"]["sessionId"], "crit")

    def test_never_over_100(self):
        # ultima-7b, 2026-10-22: a score of 100 after 11 h of waiting showed as 102.
        top, older = sess("top", waited_h=11), sess("older", waited_h=40)
        v = self.classify([top, older], {"top": {"score": score(top["statusSince"], 100)},
                                         "older": {"score": score(older["statusSince"], 96)}})
        self.assertEqual([i["eff"] for i in v["need"]], [100, 100])
        self.assertEqual(v["need"][0]["s"]["sessionId"], "older")  # at 100, the longer wait first

    def test_tie_goes_to_longest_wait(self):
        a, b = sess("a", waited_h=1), sess("b", waited_h=1.0001)
        v = self.classify([a, b], {"a": {"manual_priority": 50}, "b": {"manual_priority": 50}})
        self.assertEqual(v["need"][0]["s"]["sessionId"], "b")

    def test_snooze_holds_until_time_or_status_change(self):
        a = sess("a")
        until = (NOW + dt.timedelta(hours=1)).timestamp() * 1000
        e = {"snooze_until": until, "snooze_stamp": a["statusSince"]}
        self.assertEqual(len(self.classify([a], {"a": e})["pending"]), 1)
        a2 = dict(a, statusSince=a["statusSince"] + 5000)  # session moved on
        self.assertEqual(len(self.classify([a2], {"a": e})["need"]), 1)
        e_past = dict(e, snooze_until=(NOW - dt.timedelta(minutes=1)).timestamp() * 1000)
        self.assertEqual(len(self.classify([a], {"a": e_past})["need"]), 1)

    def test_scorer_pending_until_and_nothing(self):
        a, b = sess("a", status="idle"), sess("b", status="idle")
        v = self.classify([a, b], {
            "a": {"score": score(a["statusSince"], pending_until="2026-10-01T09:00")},
            "b": {"score": score(b["statusSince"], kind="nothing")},
        })
        self.assertEqual([i["s"]["sessionId"] for i in v["pending"]], ["a"])
        self.assertEqual([i["s"]["sessionId"] for i in v["free"]], ["b"])

    def test_stale_score_still_ranks(self):
        a = sess("a")
        v = self.classify([a], {"a": {"score": score(123, 90)}})
        self.assertEqual(v["need"][0]["base"], 90)
        self.assertIsNone(v["need"][0]["fresh"])

    def test_main_answer_nothing_needed_wins_over_the_kind(self):
        a = sess("a", status="idle")
        sc = score(a["statusSince"], 14, kind="action", level=0, level_p=0.66)
        self.assertEqual(len(self.classify([a], {"a": {"score": sc}})["free"]), 1)
        sc.update(level_p=0.45)  # jev is not sure: the user decides
        self.assertEqual(len(self.classify([a], {"a": {"score": sc}})["need"]), 1)
        sc.update(level=1, level_p=0.9)
        self.assertEqual(len(self.classify([a], {"a": {"score": sc}})["need"]), 1)

    def test_an_open_prompt_needs_the_user_whatever_the_score_says(self):
        a = sess("a", status="waiting")  # a session on 2026-10-09 16:04: a permission prompt
        for sc in (score(a["statusSince"], 23, kind="decision", level=0, level_p=0.54),
                   score(a["statusSince"], 4, kind="nothing"),
                   score(a["statusSince"], 30, pending_until="2099-01-01 09:00")):
            v = self.classify([a], {"a": {"score": sc}})
            self.assertEqual((len(v["need"]), len(v["free"]), len(v["pending"])), (1, 0, 0), sc)

    def test_dismissed_until_change(self):
        a = sess("a", status="idle")
        v = self.classify([a], {"a": {"dismissed_stamp": a["statusSince"]}})
        self.assertEqual(len(v["free"]), 1)

    def test_done_never_hides_an_open_question(self):
        a = sess("a", status="waiting")
        v = self.classify([a], {"a": {"dismissed_stamp": a["statusSince"]}})
        self.assertEqual((len(v["need"]), len(v["free"])), (1, 0))


class PendingTest(unittest.TestCase):
    def test_pending_hides_until_woken(self):
        a = dict(sess("a", status="idle"), pid=7)
        st = {"sessions": {}, "pending": {"a": {"pid": 7, "until": NOW.timestamp() * 1000 + H, "note": "the reply from the bank"}}}
        v = q.classify([a], st, NOW, DEFAULT_CONFIG)
        self.assertEqual(v["pending"][0]["why"], "the reply from the bank")
        st["pending"]["a"]["pid"] = 8  # another process of the same conversation
        v = q.classify([a], st, NOW, DEFAULT_CONFIG)
        self.assertEqual(len(v["need"]), 1)

    def test_open_question_shows_although_the_session_set_a_wait(self):
        a = dict(sess("a", status="waiting"), pid=7)
        st = {"sessions": {}, "pending": {"a": {"pid": 7, "until": NOW.timestamp() * 1000 + H, "note": "x"}}}
        v = q.classify([a], st, NOW, DEFAULT_CONFIG)
        self.assertEqual((len(v["need"]), len(v["pending"])), (1, 0))
        a["status"] = "idle"
        self.assertEqual(len(q.classify([a], st, NOW, DEFAULT_CONFIG)["pending"]), 1)

    def test_wait_with_a_request_is_a_todo_until_done(self):
        a = dict(sess("a", status="idle"), pid=7)
        p = {"pid": 7, "until": NOW.timestamp() * 1000 + H, "note": "export", "needs": "Send the draft"}
        st = {"sessions": {}, "pending": {"a": p}}
        v = q.classify([a], st, NOW, DEFAULT_CONFIG)
        self.assertEqual((v["todo"][0]["needs"], v["todo"][0]["why"], v["need"]), ("Send the draft", "export", []))
        p["needs_done"] = True
        self.assertEqual(len(q.classify([a], st, NOW, DEFAULT_CONFIG)["pending"]), 1)

    def test_snoozed_request_comes_back_at_the_snooze_time(self):
        a = dict(sess("a", status="idle"), pid=7)
        snooze = NOW.timestamp() * 1000 + 0.5 * H
        st = {"sessions": {"a": {"snooze_until": snooze, "snooze_stamp": a["statusSince"]}},
              "pending": {"a": {"pid": 7, "until": NOW.timestamp() * 1000 + H, "needs": "Sign"}}}
        v = q.classify([a], st, NOW, DEFAULT_CONFIG)
        self.assertEqual(v["pending"][0]["wake"], snooze)

    def test_undelivered_wakeup_needs_the_user(self):
        a = sess("a", status="idle")
        st = {"sessions": {"a": {"score": score(a["statusSince"], 20, kind="nothing"),
                                 "woke": {"delivered": False, "stamp": a["statusSince"]}}}}
        v = q.classify([a], st, NOW, DEFAULT_CONFIG)
        self.assertEqual(v["need"][0]["base"], q.WOKE_MIN_PRIORITY)

    def test_snooze_and_done_win_over_an_undelivered_wakeup(self):
        a = sess("a", status="idle")
        woke = {"delivered": False, "stamp": a["statusSince"]}
        until = (NOW + dt.timedelta(days=3)).timestamp() * 1000
        st = {"sessions": {"a": {"woke": woke, "snooze_until": until, "snooze_stamp": a["statusSince"]}}}
        v = q.classify([a], st, NOW, DEFAULT_CONFIG)
        self.assertEqual((len(v["need"]), v["pending"][0]["wake"]), (0, until))
        st = {"sessions": {"a": {"woke": woke, "dismissed_stamp": a["statusSince"]}}}
        self.assertEqual(len(q.classify([a], st, NOW, DEFAULT_CONFIG)["free"]), 1)

    def test_next_event(self):
        p = {"check": "true", "every_min": 5, "last_check": 1000}
        self.assertEqual(q.next_event_ms(p, 0), 1000 + 300000)
        p["until"] = 2000
        self.assertEqual(q.next_event_ms(p, 0), 2000)


class WhenTest(unittest.TestCase):
    def test_parse_when(self):
        self.assertEqual(q.parse_when("+2h", NOW), NOW + dt.timedelta(hours=2))
        self.assertEqual(q.parse_when("16:30", NOW), NOW.replace(hour=16, minute=30))
        self.assertEqual(q.parse_when("09:00", NOW), (NOW + dt.timedelta(days=1)).replace(hour=9, minute=0))
        self.assertEqual(q.parse_when("2026-10-02 08:15", NOW), dt.datetime(2026, 10, 2, 8, 15))
        self.assertIsNone(q.parse_when("soon", NOW))

    def test_presets(self):
        p = {k: when for k, _, when in q.snooze_presets(NOW)}  # NOW is a Wednesday
        self.assertEqual(p["3"], dt.datetime(2026, 10, 1, 9, 0))
        self.assertEqual(p["4"], dt.datetime(2026, 10, 5, 9, 0))


if __name__ == "__main__":
    unittest.main()
