"""One scorer for all sessions, so the scores can be compared. Default
(config "scorer": "claude"): a headless `claude -p` call on your own Claude
Code login, which also writes the ask and the reason, about $0.010 per call on
Sonnet 5.5. Alternative ("scorer": "jev"): jev (jev.py, a TypeSafe or
OpenRouter key, about $0.0001 per call); then a small Claude model
("ask_model", Haiku 5.5) writes the one-line request for the sessions that
need the user (write_ask)."""
import asyncio
import datetime as dt
import json
import os

from . import ask, store

SCHEMA = {
    "type": "object",
    "properties": {
        "priority": {"type": "integer", "minimum": 1, "maximum": 100},
        "kind": {"type": "string",
                 "enum": ["decision", "action", "login", "review", "info", "nothing"]},
        "ask": {"type": "string"},
        "effort_min": {"type": "integer", "minimum": 0},
        "deadline": {"type": "string"},
        "pending_until": {"type": "string"},
        "reason": {"type": "string"},
        "company": {"type": "string"},
    },
    "required": ["priority", "kind", "ask", "effort_min", "deadline",
                 "pending_until", "reason", "company"],
    "additionalProperties": False,
}

SYSTEM = """You are the triage scorer for {who}. \
{User} runs many Claude Code sessions in parallel and can attend to one at a time. \
For ONE session you read what it last said and what is on its screen, and you rate \
how important it is that {user} attends to it NOW, compared with all their other work.

Scale (scores must be comparable across sessions, do not inflate):
- 90-100: drop everything. Live security or data exposure, production broken for \
clients, a legal, tax or customs deadline today or tomorrow.
- 70-89: today. A client-facing message that is ready and only waits for a send, a \
deadline within 3 days, another person (colleague or client) blocked by {user}.
- 40-69: this week. Decisions that unblock multi-step work, internal launches.
- 1-39: whenever. Reports with no action, optional improvements, personal admin \
with no deadline.
Judge the business consequence of NOT acting, not the tone of the session. A long \
report is not urgent by itself. {User}'s own priority rules below override this scale.

Fields:
- kind: decision ({user} must choose), action ({user} must do something themselves: \
send, sign, ship, click, pay), login ({user} must log in or authenticate somewhere), \
review (read and approve), info (report only), nothing (the session is done or says \
that nothing is needed from {user}).
- ask: what {user} must do now, imperative, max 100 characters, English. Name what \
must be decided or done; do not quote option labels or say which option is \
recommended.
- effort_min: realistic minutes {user} needs for the ask.
- deadline: YYYY-MM-DD of a real deadline that the text states, else "".
- pending_until: ONLY when the session says that nothing is needed from {user} until a \
specific time (for example "Next: the launch check on 1 October at 09:00. Nothing \
needs your input before then."), that time as YYYY-MM-DDTHH:MM in {user}'s local time \
(convert a time in another time zone to the local time). Else "".
- reason: why this score, max 90 characters.
- company: the company or area, one or two words."""


def system_prompt():
    role = store.load_config()["user_role"]
    return store.say(SYSTEM.replace("{who}", "{user}" + (f", {role}" if role else "")))


def build_prompt(sess, screen, last_text, now):
    # No waiting time here: the queue adds its own bonus for waiting, and
    # the scorer would count it twice.
    tz = dt.datetime.now().astimezone().strftime("%Z, UTC%z")
    parts = [
        f"NOW: {now.strftime('%A %Y-%m-%d %H:%M')} ({store.user()}'s local time, {store.local_zone()}, {tz})",
        f"SESSION: {sess.get('name', '')}  (folder {sess['cwd']})",
        f"STATE: {sess['status']} {sess.get('waitingFor', '')}",
        "",
        "=== WHAT IS ON ITS SCREEN NOW (bottom of the terminal) ===",
        screen.strip() or "(not available)",
        "",
        "=== ITS LAST MESSAGE (from the transcript) ===",
        last_text.strip() or "(none)",
        "",
        "=== THE USER'S PRIORITY RULES ===",
        store.priorities_text().strip() or "(none)",
    ]
    return "\n".join(parts)


last_error = None  # shown in the focus window until a call succeeds again


async def score(sess, screen, last_text, cfg, now=None):
    """Returns the score dict, or None on failure (logged, and kept in
    last_error so the focus window shows it)."""
    global last_error
    now = now or dt.datetime.now()
    if cfg.get("scorer") != "jev":
        return await score_claude(sess, screen, last_text, cfg, now)
    from . import jev
    try:
        result = await jev.score(sess, screen, last_text, cfg, now)
    except Exception as ex:  # network, credit, API change: never crash the engine
        last_error = f"jev: {ex!r}"[:200]
        store.log(f"score error {sess.get('name')}: {ex!r}")
        return None
    last_error = None
    result["scored_for"] = sess["statusSince"]
    result["scored_at"] = int(now.timestamp() * 1000)
    # jev writes no text. When the session needs the user and asks no open
    # question (whose own text is best), a small Claude model writes the line.
    if cfg.get("ask_model") and result["kind"] not in ("info", "nothing") \
            and not ask.open_question(screen, last_text, sess["status"]):
        line, cost = await write_ask(sess, screen, last_text, cfg)
        if line:
            result.update(ask=line, ask_model=cfg["ask_model"], ask_cost_usd=cost)
    return result


ASK_SCHEMA = {"type": "object", "properties": {"ask": {"type": "string"}},
              "required": ["ask"], "additionalProperties": False}

ASK_SYSTEM = """You write one line in {user}'s queue of Claude Code sessions: what {user} \
must do in this session NOW, so that {user} can act without reading the session first.

- Write the immediate next step that waits on {user}. It is usually the explicit request \
near the end of the last message (often under a heading such as "Needed from you", \
"Next", "One step for you" or "What you need to do"), or the "recap:" line on the screen.
- Not what the session did, not work that it or another session will do, and not \
decisions that it only lists for later.
- Imperative, at most 100 characters, in the language of the last message.
- Name the concrete thing: the page, file, button, command or person."""


def ask_prompt(sess, screen, last_text):
    return "\n".join([
        f"SESSION: {sess.get('name', '')}",
        "",
        "=== WHAT IS ON ITS SCREEN NOW (bottom of the terminal) ===",
        screen.strip() or "(not available)",
        "",
        "=== ITS LAST MESSAGE ===",
        last_text.strip() or "(none)",
    ])


async def write_ask(sess, screen, last_text, cfg):
    """(line, cost in USD) from cfg["ask_model"]; ("", cost) on failure, and
    the row keeps the line that ask.extract found."""
    data = await _claude(ask_prompt(sess, screen, last_text), store.say(ASK_SYSTEM), ASK_SCHEMA,
                         cfg["ask_model"], sess.get("name"))
    line = ((data or {}).get("structured_output") or {}).get("ask", "")
    return ask.tidy(line) if line else "", (data or {}).get("total_cost_usd") or 0


async def _claude(prompt, system, schema, model, name):
    """One headless `claude -p` call with a JSON schema: Claude Code's JSON
    output, or None on failure (logged; last_error says why)."""
    global last_error
    args = [
        "claude", "-p", "--model", model,
        "--output-format", "json", "--no-session-persistence",
        "--tools", "", "--settings", '{"disableAllHooks":true}',
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--system-prompt", system, "--json-schema", json.dumps(schema),
    ]
    # The scorer needs no thinking, MCP servers, CLAUDE.md or memory: without
    # them a call has about 3.5k input tokens instead of about 10.5k. Each
    # prompt is unique, so a 5-minute cache write (1.25x input price) is used
    # instead of Claude Code's default 1-hour write (2x). Measured: about
    # $0.010 per call on Sonnet 5.5, down from about $0.038.
    env = dict(os.environ, MAX_THINKING_TOKENS="0", CLAUDE_CODE_DISABLE_CLAUDE_MDS="1",
               CLAUDE_CODE_DISABLE_AUTO_MEMORY="1", FORCE_PROMPT_CACHING_5M="1")
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=store.DIR, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=180)
        data = json.loads(out.decode() or "{}")
        if data.get("is_error") or not isinstance(data.get("structured_output"), dict):
            store.log(f"{model} failed {name}: {out.decode()[:300]} {err.decode()[:300]}")
            last_error = f"claude: {(err.decode() or out.decode()).strip()[:160]}"
            return None
        last_error = None
        return data
    except (asyncio.TimeoutError, OSError, ValueError) as ex:  # e.g. no `claude` on PATH
        store.log(f"{model} error {name}: {ex!r}")
        last_error = f"claude: {ex!r}"[:200]
        return None


async def score_claude(sess, screen, last_text, cfg, now=None):
    """Returns the score dict, or None on failure (logged, and kept in
    last_error so the focus window shows it)."""
    now = now or dt.datetime.now()
    data = await _claude(build_prompt(sess, screen, last_text, now), system_prompt(), SCHEMA,
                         cfg["scorer_model"], sess.get("name"))
    if data is None:
        return None
    result = data["structured_output"]
    result["scored_for"] = sess["statusSince"]
    result["cost_usd"] = data.get("total_cost_usd")
    result["model"] = cfg["scorer_model"]
    result["scored_at"] = int(now.timestamp() * 1000)
    return result
