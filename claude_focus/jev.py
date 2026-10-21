"""Score a session with jev (TypeSafe's System One model): typed answers with
probabilities, no text generation. About $0.0001 and 0.4 s per call
($0.042 per million input tokens; answers are free).

jev answers three questions: the priority (a score on five levels), the kind
of request (a choice) and the effort (a choice). The one-line request text
comes from the session itself (ask.py), and scorer.write_ask replaces it when
the session needs the user; the company comes from the folder name."""
import datetime as dt
import os

from . import ask, store

USD_PER_INPUT_TOKEN = 0.042 / 1_000_000
CREDENTIALS = os.path.expanduser("~/.config/claude-focus/credentials")

# Lowest to highest. The share of the top level decides how high a session
# ranks: priority = expected level / 4 * 100.
LEVELS = [
    "Nothing is needed from {user}: the session is done, or it only reports",
    "Whenever: optional improvements, personal admin with no deadline, a small optional question",
    "This week: a decision that unblocks multi-step work, an internal launch, a review with no date",
    "Today: a client-facing message ready to send, a deadline within 3 days, a colleague or client blocked by {user}",
    "Drop everything: live security or data exposure, production broken for clients, a legal, tax or customs deadline today or tomorrow",
]
LEVEL_SHORT = ["nothing needed", "whenever", "this week", "today", "drop everything"]
KINDS = {
    "decision": "{User} must choose between options or answer a question",
    "action": "{User} must do something themselves: send, sign, ship, click, pay",
    "login": "{User} must log in or authenticate somewhere",
    "review": "{User} must read and approve something",
    "info": "The session only reports; {user} only needs to read it",
    "nothing": "The session is done or says that nothing is needed from {user}",
}
EFFORTS = {"1": "About 1 minute", "5": "About 5 minutes",
           "15": "About 15 minutes", "30": "30 minutes or more"}

_client = None


def api_key():
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return key
    try:
        with open(CREDENTIALS) as f:
            for line in f:
                if line.startswith("TYPESAFE_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def client(cfg):
    global _client
    if _client is None:
        from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
        key = api_key()
        if not key:
            raise RuntimeError(f"no jev key: set TYPESAFE_API_KEY in {CREDENTIALS}")
        _client = AsyncTypeSafeClient(api_key=key, model=cfg["jev_model"],
                                      retry=RetryPolicy(max_retries=3))
    return _client


def questions():
    from typesafe_sdk import Choice, Score
    role = store.load_config()["user_role"]
    who = "{user}" + (f", {role}," if role else "")
    return {
        "priority": Score(
            instructions=store.say(
                f"How urgently must {who} attend to this Claude Code session now, compared with "
                "all their other work? Judge the business consequence of not acting, not the tone "
                "or length of the session. {User}'s own priority rules are in the state."),
            criteria=[store.say(x) for x in LEVELS]),
        "kind": Choice(instructions=store.say("What does the session need from {user} now?"),
                       criteria={k: store.say(v) for k, v in KINDS.items()}),
        "effort": Choice(instructions=store.say("How long will {user} need for what the session asks?"),
                         criteria=EFFORTS),
    }


def to_result(answers, sess, screen, last_text, input_tokens, model, companies=None):
    """Map jev's typed answers to the queue's score fields (pure; tested)."""
    pr = answers["priority"]
    level = max(0.0, min(4.0, float(pr["score"])))
    # Level keys come back as ints from the SDK and as strings in JSON.
    probs = {str(k): float(v) for k, v in (pr.get("probabilities") or {}).items()}
    top = max(range(len(LEVELS)), key=lambda i: probs.get(str(i), 0))
    top_p = probs.get(str(top), 0)
    kind = answers["kind"]["choice"]
    return {
        "priority": max(1, min(100, round(level / 4 * 100))),
        "kind": kind,
        "ask": ask.extract(screen, last_text, sess["status"]) or store.say(KINDS[kind]),
        "effort_min": int(answers["effort"]["choice"]),
        "deadline": "",
        "pending_until": "",
        "reason": f"jev: {LEVEL_SHORT[top]} ({top_p:.0%}), confidence {float(pr.get('confidence', 0)):.2f}",
        "level": top, "level_p": round(top_p, 2),  # the main answer: 0 = nothing needed
        "company": ask.company(sess["cwd"], companies or {}),
        "confidence": float(pr.get("confidence", 0)),
        "cost_usd": (input_tokens or 0) * USD_PER_INPUT_TOKEN,
        "model": model,
    }


async def score(sess, screen, last_text, cfg, now=None):
    now = now or dt.datetime.now()
    state = {
        "now": f"{now.strftime('%A %Y-%m-%d %H:%M')} ({store.user()}'s local time, {store.local_zone()})",
        "session": sess.get("name", ""),
        "folder": sess["cwd"],
        "state": f"{sess['status']} {sess.get('waitingFor', '')}".strip(),
        "screen": screen,
        "last_message": last_text,
        "priority_rules": store.priorities_text(),
    }
    r = await client(cfg).system_one(state=state, questions=questions())
    answers = {k: v.model_dump() for k, v in r.answers.items()}
    return to_result(answers, sess, screen, last_text, r.usage.input_tokens, r.model, cfg["companies"])
