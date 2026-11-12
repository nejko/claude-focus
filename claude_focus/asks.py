"""A question that a session queues with claude-focus instead of opening the
AskUserQuestion dialog at once (`claude-focus ask`).

An open dialog blocks its session: wake-ups, Monitor events and the results
of background tasks wait until the user answers. On 2026-11-11 that stopped
the ultima2 coordinator for 2.5 hours and then for 14 hours, with three merge
requests paused. A queued question shows under YOUR TO-DOS (queue.ask_of),
ranked first once waiting costs something (--costly-after). The session
works on, or ends its turn with a pending wait that still wakes it. When the
user comes to the to-do, Enter types here_message into the session, and the
session asks the question with AskUserQuestion then, while the user is at
the keyboard (App.go_selected)."""
import json
import time

from . import store

PREFIX = "[claude-focus ask]"


def validate(questions):
    """The reason the questions are not a valid AskUserQuestion call, or None:
    1 to 4 questions, each with text and 2 to 4 options with a label."""
    if not isinstance(questions, list) or not 1 <= len(questions) <= 4:
        return "give 1 to 4 questions"
    for n, qn in enumerate(questions, 1):
        if not isinstance(qn, dict) or not str(qn.get("question") or "").strip():
            return f"question {n} has no text"
        opts = qn.get("options")
        if not isinstance(opts, list) or not 2 <= len(opts) <= 4:
            return f"question {n} needs 2 to 4 options"
        if any(not isinstance(o, dict) or not str(o.get("label") or "").strip() for o in opts):
            return f"question {n} has an option without a label"
    return None


def option(text):
    """'label::description' (the description may be left out) -> an option."""
    label, _, about = text.partition("::")
    return {"label": label.strip(), "description": about.strip()}


def put(sess, questions, default="", costly_after=None):
    """Queue the questions of this session (one record per session: a new one
    replaces the old). Returns the record."""
    sid = sess["sessionId"]
    n = [0]

    def fn(st):
        asks = st.setdefault("asks", {})
        n[0] = st.get("ask_seq", 0) + 1
        st["ask_seq"] = n[0]
        asks[sid] = {"id": f"q{n[0]}", "pid": sess.get("pid"), "name": sess.get("name"), "cwd": sess.get("cwd"),
                     "questions": questions, "default": default, "costly_after": costly_after,
                     "at": int(time.time() * 1000)}
    store.update_state(fn)
    rec = store.load_state()["asks"][sid]
    store.log(f"ask {rec['id']} queued by {sess.get('name')}: {questions[0]['question'][:120]}")
    return rec


def clear(sid):
    """Remove the session's queued question; returns it, or None."""
    gone = []
    store.update_state(lambda st: gone.append((st.get("asks") or {}).pop(sid, None)))
    return gone[0]


def get(sid):
    return (store.load_state().get("asks") or {}).get(sid)


def here_message(rec):
    """What Enter on the to-do types into the session: the user is here now."""
    first = rec["questions"][0]["question"]
    more = f" (and {len(rec['questions']) - 1} more)" if len(rec["questions"]) > 1 else ""
    return store.say(f'{PREFIX} {{User}} is here for your question {rec["id"]}: "{first[:200]}"{more}. Ask it '
                     "now with AskUserQuestion, with the questions and options you queued (`claude-focus ask "
                     "--show` prints them). The queued copy is gone; if you cannot ask now, queue it again "
                     "with claude-focus ask.")


def show(rec):
    """The queued questions as the input of an AskUserQuestion call."""
    return json.dumps({"questions": rec["questions"]}, ensure_ascii=False, indent=1)
