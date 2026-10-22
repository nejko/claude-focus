"""Pure queue logic: sort sessions into need-you / working / pending / free,
and compute the effective priority. No I/O here, so tests can drive it."""
import datetime as dt

UNSCORED_PRIORITY = 50
# A score comes about 17 s after a turn ends (27 s at the 90th percentile,
# state.json on 2026-10-22); until then, or this long, the session waits
# under WORKING as "scoring".
SCORING_GRACE_S = 120
# Claude Code's statuses: busy (a turn runs), shell (the turn ended but
# background shells still run and will wake the session), idle (done,
# waits for the next prompt), waiting (a question or permission prompt).
# Like Claude Code's own agent view, busy and shell count as working; an
# unknown future status is treated as working too, never as "needs the user".
# But a shell session's turn has ended: a wait it set runs, a wake-up can be
# typed into its prompt, and it is scored, since its turn can have asked the
# user for something (shell_asks_user).
NEEDS_USER = ("idle", "waiting")
AT_PROMPT = ("idle", "shell")  # the turn has ended and the prompt takes a message


def working(s):
    return s["status"] not in NEEDS_USER


def turn_ended(s):
    return s["status"] in NEEDS_USER + AT_PROMPT
WOKE_MIN_PRIORITY = 70  # a wake-up that could not be delivered needs the user


def entry(state, sid):
    return state["sessions"].get(sid, {})


def fresh_score(e, stamp):
    s = e.get("score")
    return s if s and s.get("scored_for") == stamp else None


def parse_local(s):
    """'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM' (local time) -> datetime or None."""
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s.strip()[:16], fmt)
        except ValueError:
            continue
    return None


def effective(sess, e, now, cfg):
    """(effective, base, waited_hours). Base is the manual priority if set,
    else the latest score (fresh or stale), else UNSCORED_PRIORITY.

    The scorer already weighs deadlines (it is rerun every 12 h with the
    current date), so the only bonus here is a small one for waiting: it
    stops a neglected session from sinking forever, but it cannot lift a
    medium item over a critical one. The effective priority stays at most
    100, like the score: among sessions at 100, the longest wait goes first
    (classify sorts by the wait next)."""
    score = e.get("score") or {}
    base = e.get("manual_priority") or score.get("priority") or UNSCORED_PRIORITY
    waited_h = max(0.0, (now.timestamp() * 1000 - sess["statusSince"]) / 3.6e6)
    aging = min(cfg["aging_max"], waited_h * cfg["aging_points_per_hour"])
    return min(100, base + aging), base, waited_h


def next_event_ms(p, now_ms):
    """When a pending wait next does something: its deadline or next check."""
    times = []
    if p.get("until"):
        times.append(p["until"])
    if p.get("check"):
        times.append((p.get("last_check") or now_ms) + p.get("every_min", 5) * 60000)
    return min(times) if times else now_ms


NOTHING_P = 0.5  # "nothing needed" as jev's main answer, with at least this share


def nothing_needed(sc):
    """The session needs nothing from the user: the kind says so, or jev's main
    answer to the priority question does. The two answers come from separate
    questions and can disagree (a resumed session's screen showed a Claude
    Code notice, so the kind said "action" while the priority said "nothing
    needed" at 66%); the priority question is the main one."""
    return sc.get("kind") == "nothing" or (sc.get("level") == 0 and sc.get("level_p", 0) >= NOTHING_P)


def open_needs(p):
    """What a waiting session asks the user to do meanwhile (pending --needs),
    until they mark it done; None when it only waits."""
    return p.get("needs") if p and not p.get("needs_done") else None


def seen_report(state, s, sc):
    """The turn only reported (kind info), and the user has seen it: their
    keys were in its pane after the turn ended (state "seen", App._note_seen).
    It needs nothing more from them. A decision, an action, a review or a
    login stays: seeing it is not doing it."""
    seen = state.get("seen", {}).get(s["sessionId"])
    return bool(sc) and sc.get("kind") == "info" and bool(seen) and seen > s["statusSince"]


def shell_asks_user(state, s, e, sc):
    """A shell session (its turn ended, background shells it started still
    run) usually waits for its own work and stays under WORKING. It needs the
    user when its turn ended with a request: pick a design on a page that a
    background server shows, try a dev server. The scorer has read that turn
    and did not say that nothing is needed, and the user has not marked it
    done or seen its report (then it is working again: the shells wake it
    when they end)."""
    return (s["status"] == "shell" and bool(sc) and not nothing_needed(sc)
            and e.get("dismissed_stamp") != s["statusSince"] and not seen_report(state, s, sc))


def hidden_by_wait(state, s):
    """The wait that keeps this session out of NEED YOU, or None. An open
    question (or permission prompt) always shows, also when the session set
    a wait earlier in the same turn; the wait itself stays set."""
    return pending_of(state, s) if s["status"] != "waiting" else None


def pending_of(state, s):
    p = state.get("pending", {}).get(s["sessionId"])
    return p if p and p.get("pid") in (None, s["pid"]) else None


def classify(sessions, state, now, cfg):
    """Returns dict with lists: need, todo, working, pending, free. `need` is
    sorted, highest effective priority first; ties go to the longest wait.
    `todo` (waits that left the user a task) is sorted oldest first."""
    out = {"need": [], "todo": [], "working": [], "pending": [], "free": []}
    now_ms = now.timestamp() * 1000
    for s in sessions:
        e = entry(state, s["key"])
        stamp = s["statusSince"]
        item = {"s": s, "e": e, "score": e.get("score"), "fresh": fresh_score(e, stamp)}
        # A shell session that waits, has an undelivered wake-up, or asks the
        # user goes where an idle one would; else it works on its shells.
        woke = e.get("woke")
        if working(s) and not (s["status"] == "shell" and (
                hidden_by_wait(state, s) or (woke and not woke.get("delivered") and woke.get("stamp") == stamp)
                or shell_asks_user(state, s, e, item["fresh"]))):
            out["working"].append(item)
            continue
        p = hidden_by_wait(state, s)
        if p:
            item["pending"] = p
            item["wake"] = next_event_ms(p, now_ms)
            item["why"] = p.get("note") or ("waits for a check" if p.get("check") else "waits")
            # A session can wait AND leave the user a task (a draft they must send).
            # It does not wait for their answer, so it goes under YOUR TO-DOS,
            # not NEED YOU; its wait still runs.
            needs = open_needs(p)
            until = e.get("snooze_until")
            snoozed = until and e.get("snooze_stamp") == stamp and now_ms < until
            if needs and not snoozed:
                item["needs"] = needs
                item["eff"], item["base"], item["waited_h"] = effective(s, e, now, cfg)
                out["todo"].append(item)
                continue
            if needs:  # snoozed: back at the snooze time, or when the wait wakes it
                item["wake"] = min(item["wake"], until)
                item["why"] = e.get("snooze_note") or "snoozed"
            out["pending"].append(item)
            continue
        # The user's own answer (done, snooze) wins over an undelivered wake-up:
        # the engine still types that wake-up when the prompt is empty again.
        # "Done" never hides an open question: it would never come back.
        if e.get("dismissed_stamp") == stamp and s["status"] != "waiting":
            item["why"] = "you marked it done"
            out["free"].append(item)
            continue
        until = e.get("snooze_until")
        if until and e.get("snooze_stamp") == stamp and now_ms < until:
            item["wake"] = until
            item["why"] = e.get("snooze_note") or "snoozed"
            out["pending"].append(item)
            continue
        woke = e.get("woke")
        if woke and not woke.get("delivered") and woke.get("stamp") == stamp:
            item["woke"] = woke
            eff, base, waited = effective(s, e, now, cfg)
            item["base"] = max(base, WOKE_MIN_PRIORITY)
            item["eff"], item["waited_h"] = max(eff, item["base"]), waited
            out["need"].append(item)
            continue
        sc = item["fresh"]
        # An open question or permission prompt always needs the user, whatever
        # the score says: the scorer also reads the last message, which can
        # say "nothing to do" while a later prompt waits on the screen.
        if sc and s["status"] != "waiting":
            pu = parse_local(sc.get("pending_until"))
            if pu and pu > now:
                item["wake"] = pu.timestamp() * 1000
                item["why"] = sc.get("ask") or "waits until then"
                out["pending"].append(item)
                continue
            if nothing_needed(sc):
                item["why"] = sc.get("ask") or "nothing needed"
                out["free"].append(item)
                continue
            if seen_report(state, s, sc):
                item["why"], item["seen"] = sc.get("ask") or "you have seen its report", True
                out["free"].append(item)
                continue
        # Not scored yet: under WORKING as "scoring" for a while, so it does not
        # jump into NEED YOU with an older state's priority and then leave. An
        # open question needs no score, and a scorer that fails or lags lets
        # it into NEED YOU after SCORING_GRACE_S.
        if not sc and s["status"] != "waiting" and now_ms - stamp < SCORING_GRACE_S * 1000:
            item["scoring"] = True
            out["working"].append(item)
            continue
        item["eff"], item["base"], item["waited_h"] = effective(s, e, now, cfg)
        out["need"].append(item)
    out["need"].sort(key=lambda i: (-i["eff"], -i["waited_h"]))
    out["todo"].sort(key=lambda i: -i["waited_h"])
    out["pending"].sort(key=lambda i: i["wake"])
    return out


# --- snooze presets --------------------------------------------------------

def snooze_presets(now):
    tomorrow9 = (now + dt.timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    days_to_monday = (7 - now.weekday()) % 7 or 7
    monday9 = (now + dt.timedelta(days=days_to_monday)).replace(
        hour=9, minute=0, second=0, microsecond=0)
    return [
        ("1", "1 hour", now + dt.timedelta(hours=1)),
        ("2", "3 hours", now + dt.timedelta(hours=3)),
        ("3", "tomorrow 09:00", tomorrow9),
        ("4", "Monday 09:00", monday9),
    ]


def parse_when(text, now):
    """'HH:MM' (today, or tomorrow if past), 'YYYY-MM-DD HH:MM', '+2h', '+30m', '+1d'."""
    t = text.strip().lower()
    try:
        if t.startswith("+") and t[-1] in "mhd":
            n = float(t[1:-1])
            unit = {"m": "minutes", "h": "hours", "d": "days"}[t[-1]]
            return now + dt.timedelta(**{unit: n})
        if len(t) <= 5 and ":" in t:
            h, m = (int(x) for x in t.split(":"))
            when = now.replace(hour=h, minute=m, second=0, microsecond=0)
            return when if when > now else when + dt.timedelta(days=1)
    except ValueError:
        return None
    return parse_local(text)
