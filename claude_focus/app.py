"""The focus screen: every session in sections (the selected row, by default
the one that needs the user most, is highlighted), the free terminals, and the
keys. Runs in its own iTerm window or herdr pane. It also runs the waker for
pending waits. The terminal is behind a bridge (iterm.py, herdr.py)."""
import asyncio
import datetime as dt
import json
import os
import shlex
import shutil
import sys
import termios
import time
import tty as ttymod

from . import costs, folders, look
from . import pending as pend
from . import queue as q
from . import restore, scorer, sessions as sessions_mod, store, terminal
from .look import B, R, bg, fg, fit, mix, width
from .screen import EMPTY
from .terminal import SHELLS

FOCUS_BAR = "\033[1;30;42m"  # the title bar while this window has the keys: bold black on green
STABLE_SECONDS = 15       # a status must hold this long before it is scored
STATUS_FILE = os.path.join(store.DIR, "engine.json")
TUI_FILE = restore.TUI_FILE
BIN = shutil.which("claude-focus") or os.path.join(  # installed, else run from the checkout
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "claude-focus")
RESCORE_AFTER_H = 12      # rescore a waiting session twice a day
CHECK_TIMEOUT = 120       # seconds for one pending check command
SNAPSHOT_SECONDS = 10     # how often the engine saves the layout for a restore
ANSWER_POLL = 0.4         # seconds between two looks at a dialog the user went to answer
SEEN_WRITE_SECONDS = 30   # while the user stays in one pane, how often its "seen" time is written
SEEN_KEEP_DAYS = 14
ENDED_GRACE = 60          # seconds after a session ended in which its pane must be back at a bare shell
EVENT_PAUSE = 0.3         # seconds: a burst of terminal events (watch_events) makes one poll
TERMINAL_CHECK = 60       # seconds between two looks of the service at which terminal runs the sessions

KIND_LABEL = {"decision": "decision", "action": "action", "login": "login",
              "review": "review", "info": "info", "nothing": "nothing"}
# How a session starts in a folder where Claude Code ran before (the step
# after the folder picker): key, label, what follows new_session_command.
START_KINDS = [("n", "New session", ""), ("c", "Continue the last session", " --continue"),
               ("r", "Resume an earlier session", " --resume")]  # --resume: Claude Code's own list
SECTIONS = [("need", "NEED YOU"), ("todo", "YOUR TO-DOS"), ("pending", "PENDING"),
            ("working", "WORKING"), ("free", "FREE"), ("terminals", "AVAILABLE TERMINALS")]
COUNT_LABEL = {"need": "need you", "todo": "to-do", "pending": "pending", "working": "working",
               "free": "free", "terminals": "terminals"}
HIGH, MID = 80, 60        # priorities shown in red and in amber
PRIORITY_CELLS = 8        # the length of a priority bar at 100
PATH_CELLS = 40           # the widest folder column; a longer path keeps its end


def fmt_wait(h):
    """Hours as 37m, 5h20m, 1.9d or 12d: at most 6 columns."""
    if h < 1:
        return f"{int(h * 60)}m"
    if h < 24:
        return f"{int(h)}h{int((h % 1) * 60):02d}m"
    return f"{h / 24:.1f}d" if h < 240 else f"{int(h / 24)}d"


def fmt_when(ms):
    t = dt.datetime.fromtimestamp(ms / 1000)
    now = dt.datetime.now()
    if t.date() == now.date():
        return t.strftime("%H:%M")
    if (t.date() - now.date()).days < 7:
        return t.strftime("%a %H:%M")
    return t.strftime("%d %b %H:%M")


def ago(t):
    """Unix s as "now" or "5h20m ago"."""
    return "now" if time.time() - t < 60 else f"{fmt_wait((time.time() - t) / 3600)} ago"


def short_path(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or (path or "").startswith(home + "/") else path or "?"


def work_label(s):
    label = {"busy": "working", "shell": "waiting for its background shells"}.get(
        s["status"], s["status"])
    return label + (" (background session)" if s.get("kind") in ("bg", "background") else "")


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def tokens(data):
    """Split one read from stdin into keys: arrow keys arrive as ESC [ A."""
    out, i = [], 0
    while i < len(data):
        if data[i] == "\x1b" and data[i + 1:i + 2] in ("[", "O") and i + 2 < len(data):
            out.append(data[i:i + 3])
            i += 3
        else:
            out.append(data[i])
            i += 1
    return out


class App:
    """One class, two roles. The ENGINE (whoever holds the engine lock: the
    background service, or else the first focus window) scores sessions,
    runs the waker and owns the hotkeys. Every focus window is a VIEW of the
    shared state. `headless` = the background service: engine only, no screen.
    `passive` = a one-off command (`claude-focus go`): a view, never the engine.
    `bridge` = the terminal (iterm.Bridge or herdr.Bridge; see terminal.py)."""

    def __init__(self, bridge, headless=False, passive=False):
        self.headless = headless
        self.passive = passive
        self.cfg = store.load_config()
        self.bridge = bridge
        self.sessions = []
        self.view = None
        self.scoring = set()      # state keys being scored now
        self.checking = set()     # session ids whose pending check runs now
        self.sem = asyncio.Semaphore(self.cfg["scorer_parallel"])
        self.mode = "main"        # main | snooze | snooze_time | priority | restore | folders
        self.buf = ""
        self.msg = ""
        self.msg_until = 0
        self.sel = None           # state key of the selected row; None = top
        self.last_frame = None
        self.engine = None        # engine lock file while this process is the engine
        self.remote = {}          # the other engine's status (engine.json) when we are a view
        self.last_status = None
        self.wake = asyncio.Event()
        self.poll_now = asyncio.Event()  # set by terminal events: poll before poll_seconds pass
        self.polled = False
        self.run_id = None        # start time of the running iTerm2 (the engine's run)
        self.snap_at = 0
        self.snap_sig = None
        self.prev_layout = None   # the last layout of the previous iTerm2 run
        self.restore_handled = None
        self.restore_lines = []
        self.answer_watch = None  # the task that waits for an answer (see _return_after_answer)
        self.focused = False      # this window has the keys (green title bar)
        self.typed_check = {}     # session id -> the statusSince whose transcript was read (pending_tick)
        self.seen_sid = None      # the session whose pane had the user's keys at the last poll
        self.seen_written = 0     # Unix ms of the last write of state["seen"]
        self.terminals = []       # the free terminals (see _free_terminals); only a focus window reads them
        self.claude_panes = {}    # iterm session id -> (session name, Unix s last seen) of panes that ran a session
        self.pal = look.DARK      # the colors now: config "theme" for the terminal's appearance
        self.appearance = "dark"  # the terminal's appearance (bridge.theme)
        self.pick = None          # the folder picker: {"term": free terminal, "sel": row}
        self.folders = []         # its folders (see folders.candidates)
        self.matches = []         # the folders that match what the user typed (self.buf)
        self.new_folder = None    # the folder it can create from what the user typed (folders.new_folder)

    # ---- data -------------------------------------------------------------

    async def poll(self):
        rows = await asyncio.get_event_loop().run_in_executor(None, sessions_mod.list_sessions)
        if rows is not None:
            self.sessions = rows
        await self.bridge.refresh()
        if not self.headless and not self.passive:
            self.terminals = await self._free_terminals()
            self.appearance = await self.bridge.theme()
            self.pal = look.palette(self.appearance, self.cfg["theme"])
        # The service is the preferred engine. A focus window is the engine
        # only while no service runs, and it hands the role back when one does.
        if self.engine and not self.headless and pend.service_alive():
            self.engine.close()
            self.engine = None
            store.log(f"engine handed to the service (window pid {os.getpid()})")
        elif self.engine is None and not self.passive and (self.headless or not pend.service_alive()):
            self.engine = pend.take_engine_lock()
            if self.engine:
                self._start_engine()
        if self.engine:
            self._note_seen()
            if self.cfg["auto_profiles"] and self.bridge.profiles:
                try:
                    await self._recolor_panes()
                except Exception as ex:  # never let it break the waker
                    store.log(f"profile switch error: {ex!r}")
            await self.pending_tick(store.load_state())
        state = store.load_state()
        self.restore_handled = state.get("restore_handled")
        if self.engine:
            self._reset_skips(state)
            self._take_rescore_requests(state)
            await self.retry_wakeups(state)
            if self.bridge.restores:
                await self._snapshot_tick(state)
        self.view = q.classify(self.sessions, state, dt.datetime.now(), self.cfg)
        self.view["terminals"] = self.terminals
        if self.engine:
            self._schedule_scores(state)
            self._write_status()
            if self.cfg["herdr_sidebar"] and self.bridge.sidebar:
                await self.bridge.report(self._marks())
        else:
            self._read_status()
        self.polled = True

    def _start_engine(self):
        store.log(f"engine started ({'background service' if self.headless else 'focus window'}, "
                  f"{self.bridge.label}, pid {os.getpid()})")
        if not self.bridge.restores:
            return  # herdr keeps its own layout and resumes the sessions in it
        self.run_id = restore.iterm_run()
        try:
            restore.prune(self.run_id)
        except OSError as ex:
            store.log(f"layouts not pruned: {ex!r}")
        asyncio.ensure_future(self._guard("auto restore", self._auto_restore()))

    async def _auto_restore(self):
        """After a restart prepared with `claude-focus reboot`: open the saved
        layout without a key press (once; see restore.auto)."""
        await asyncio.sleep(restore.AUTO_RESTORE_DELAY)
        if await restore.auto(self.bridge.conn, self.run_id, self.flash):
            self.wake.set()

    async def _snapshot_tick(self, state):
        """Save the layout, so a restore can open it after a crash."""
        if time.time() - self.snap_at < SNAPSHOT_SECONDS:
            return
        self.snap_at = time.time()
        try:
            snap = await restore.take(self.bridge, self.sessions, state)
            self.snap_sig = restore.record(snap, self.run_id, self.snap_sig)
        except Exception as ex:  # never let the snapshot break the poll
            store.log(f"snapshot error: {ex!r}")

    def _start_hotkeys(self):
        """Optional (config "hotkeys"). Only a focus window registers them:
        from the background service they did not reach iTerm2. herdr has key
        bindings of its own instead (setup adds them: `claude-focus go`,
        `claude-focus show`)."""
        if self.headless or not self.cfg.get("hotkeys") or not self.bridge.hotkeys:
            return
        asyncio.ensure_future(self._guard("hotkeys", self.bridge.watch_hotkeys({
            self.cfg["hotkey_go"]: self.go_top,
            self.cfg["hotkey_focus_window"]: self.show_focus_window,
        })))

    def _write_status(self):
        status = {"pid": os.getpid(), "mode": "service" if self.headless else "window",
                  "scoring": sorted(self.scoring), "error": scorer.last_error}
        if status != self.last_status or int(time.time()) % 30 < self.cfg["poll_seconds"]:
            self.last_status = status
            tmp = STATUS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(dict(status, at=int(time.time())), f)
            os.replace(tmp, STATUS_FILE)

    def _read_status(self):
        try:
            with open(STATUS_FILE) as f:
                self.remote = json.load(f)
        except (OSError, ValueError):
            self.remote = {}

    def _take_rescore_requests(self, state):
        keys = state.get("rescore", [])
        if not keys:
            return
        store.update_state(lambda st: st.__setitem__("rescore", []))
        for s in self.sessions:
            if s["key"] in keys and s["key"] not in self.scoring:
                self.scoring.add(s["key"])
                asyncio.ensure_future(self._score(s))

    def scoring_now(self):
        return self.scoring if self.engine else set(self.remote.get("scoring", []))

    def scorer_error(self):
        return scorer.last_error if self.engine else self.remote.get("error")

    def _reset_skips(self, state):
        busy = [s["key"] for s in self.sessions if q.working(s)
                and state["sessions"].get(s["key"], {}).get("skips")]
        if busy:
            def fn(st):
                for key in busy:
                    st["sessions"][key]["skips"] = 0
            store.update_state(fn)

    def _schedule_scores(self, state):
        now_ms = time.time() * 1000
        todo = []
        for s in self.sessions:
            key = s["key"]
            if q.working(s) or key in self.scoring or q.hidden_by_wait(state, s):
                continue
            if self._user_in(s):
                continue  # the user is in this session now; score it when they leave
            if now_ms - s["statusSince"] < STABLE_SECONDS * 1000:
                continue
            sc = q.fresh_score(state["sessions"].get(key, {}), s["statusSince"])
            if sc and now_ms - sc.get("scored_at", 0) < RESCORE_AFTER_H * 3.6e6:
                continue
            todo.append(s)
        todo.sort(key=lambda s: s["statusSince"])
        for s in todo:
            self.scoring.add(s["key"])
            asyncio.ensure_future(self._score(s))

    async def _score(self, s):
        key = s["key"]
        try:
            async with self.sem:
                screen = ""
                pane = self.pane_of(s)
                if pane:
                    screen = await self.bridge.screen_text(pane)
                last = await asyncio.get_event_loop().run_in_executor(
                    None, sessions_mod.last_assistant_text, s["cwd"], s["sessionId"])
                result = await scorer.score(s, screen, last, self.cfg)
            if result:
                store.update_state(lambda st: st["sessions"].setdefault(key, {}).__setitem__("score", result))
                line = f" + {result['ask_model']} ${result['ask_cost_usd']:.4f}" if "ask_model" in result else ""
                store.log(f"scored {s.get('name')}: {result['priority']} {result['kind']} {result['ask']}"
                          f" (${result.get('cost_usd') or 0:.4f}{line})")
                costs.record(s.get("name"), result.get("model"), result.get("cost_usd"))
                if "ask_model" in result:
                    costs.record(s.get("name"), result["ask_model"], result["ask_cost_usd"])
        finally:
            self.scoring.discard(key)
            self.wake.set()

    def _user_in(self, s):
        """True while this session's pane gets the user's keys (the active pane
        of the front window): the user usually answers at once, so a score
        would be wasted."""
        pane = self.pane_of(s)
        return pane is not None and pane == self.bridge.user_pane()

    def pane_of(self, s):
        return self.bridge.pane_of(s)

    async def _free_terminals(self):
        """The free panes, ready for a new session. In iTerm2, the ones the
        user marked: when they end a session, they switch its pane to the
        green profile (config "free_profile", Available), or the engine does
        it (_recolor_panes); oldest pane (lowest tty) first. In herdr, every
        pane at a bare shell, in herdr's order. A pane with a running Claude
        session is not free, and neither is this window."""
        taken = {self.pane_of(s) for s in self.sessions} - {None}
        own = self.bridge.own_pane()
        out = []
        for sid, where, profile, path, job in await self.bridge.pane_info():
            if not self.bridge.is_free(profile, job, self.cfg) or sid == own or sid in taken:
                continue
            here = short_path(path)
            out.append({"s": {"key": "pane:" + sid, "name": os.path.basename(here) or here},
                        "e": {}, "score": None, "fresh": None,
                        "pane": sid, "tty": where, "path": here, "job": job or ""})
        return out

    def _marks(self):
        """{pane: (section, priority, request)} of every session with a pane:
        its place in the queue, for herdr's sidebar (bridge.report)."""
        out = {}
        for sec, _ in SECTIONS:
            for i in (self.view or {}).get(sec) or []:
                pane = self.pane_of(i["s"]) if sec != "terminals" else None
                if not pane:
                    continue
                ask = {"need": (i.get("fresh") or {}).get("ask"), "todo": i.get("needs"),
                       "pending": i.get("why"), "free": (i.get("fresh") or {}).get("ask") or i.get("why")}
                label = f"wakes {fmt_when(i['wake'])}" if sec == "pending" else COUNT_LABEL[sec]
                out[pane] = (label, i.get("eff") if sec == "need" else None, ask.get(sec) or "")
        return out

    async def _recolor_panes(self):
        """Keep the green color true (config "auto_profiles"), so the user no
        longer switches profiles by hand:
        - a Claude session runs in a free (green) pane: the pane gets the
          session profile (Default);
        - a session ended and its pane is back at a bare shell: the pane is
          free (green). A pane that runs another program then is not free;
          after ENDED_GRACE s it is forgotten. A session file that is
          unreadable for a moment does not count: the pane still runs claude."""
        free, back = self.cfg["free_profile"], self.cfg["session_profile"]
        by_pane = {}
        for s in self.sessions:
            if self.pane_of(s):
                by_pane[self.pane_of(s)] = s
        now = time.time()
        seen = set()
        for sid, tty, profile, path, job in await self.bridge.pane_info():
            seen.add(sid)
            s = by_pane.get(sid)
            if s:
                self.claude_panes[sid] = (s.get("name") or s["sessionId"][:8], now)
                if profile == free and await self.bridge.set_profile(sid, back):
                    store.log(f"pane {tty}: {s.get('name')} runs in a free terminal, profile {back}")
                continue
            if sid not in self.claude_panes:
                continue
            name, last = self.claude_panes[sid]
            if (job or "").lstrip("-") in SHELLS:
                del self.claude_panes[sid]
                if profile != free and await self.bridge.set_profile(sid, free):
                    store.log(f"pane {tty}: {name} ended, the terminal is free (profile {free})")
            elif now - last > ENDED_GRACE:
                del self.claude_panes[sid]
        for sid in set(self.claude_panes) - seen:
            del self.claude_panes[sid]  # the pane closed

    # ---- waker ------------------------------------------------------------

    async def pending_tick(self, state):
        now_ms = time.time() * 1000
        live = {}
        for s in self.sessions:
            live.setdefault(s["sessionId"], []).append(s)
        for sid, p in list(state.get("pending", {}).items()):
            s = next((x for x in live.get(sid, []) if p.get("pid") in (None, x["pid"])), None)
            if s is None:
                # A session file can be unreadable for a moment while Claude
                # Code rewrites it; drop the wait only when the process is gone.
                if p.get("pid") and _pid_alive(p["pid"]):
                    continue
                pend.clear_pending(sid)
                store.log(f"pending dropped, session closed: {p.get('name')}")
                continue
            if p.get("armed_stamp") is None:
                if not q.working(s):  # the turn that set the wait has ended
                    store.update_state(lambda st: st["pending"][sid].__setitem__("armed_stamp", s["statusSince"]))
                    self._answer_seen(s, sid, p)
                continue
            if s["statusSince"] > p["armed_stamp"]:
                # The status changed after the turn that set the wait. Only a
                # prompt from the user ends it (the prompt hook usually ends it
                # first). An idle compaction, the answer to a question, a
                # background task or another session's message does not.
                if self.typed_check.get(sid) != s["statusSince"]:
                    self.typed_check[sid] = s["statusSince"]
                    if await asyncio.get_event_loop().run_in_executor(
                            None, sessions_mod.user_typed_since, s["cwd"], sid, p["armed_stamp"]):
                        pend.clear_pending(sid)
                        store.log(f"pending cleared, {store.user()} typed (seen in the transcript): {s.get('name')}")
                        continue
                if q.working(s):
                    continue  # no wake-up while it works
                store.update_state(lambda st: st["pending"][sid].__setitem__("armed_stamp", s["statusSince"]))
                store.log(f"pending kept, {s.get('name')} was active without a prompt from {store.user()}")
            self._answer_seen(s, sid, p)
            if p.get("until") and now_ms >= p["until"]:
                await self.fire(s, p, "time")
            elif (p.get("check") and sid not in self.checking
                  and now_ms - (p.get("last_check") or 0) >= p.get("every_min", 5) * 60000):
                self.checking.add(sid)
                asyncio.ensure_future(self.run_check(s, p))

    def _note_seen(self):
        """Remember when the user's keys were last in each session's pane (state
        "seen": session id -> Unix ms, kept since state "seen_since").
        `claude-focus pending` reads it: did they see an answer to their message
        before a later turn, started by a background task, set a wait?"""
        cur = next((s["sessionId"] for s in self.sessions if self._user_in(s)), None)
        now = int(time.time() * 1000)
        prev = self.seen_sid
        if cur == prev and (cur is None or now - self.seen_written < SEEN_WRITE_SECONDS * 1000):
            return
        self.seen_sid, self.seen_written = cur, now

        def fn(st):
            seen = st.setdefault("seen", {})
            for sid in {prev, cur} - {None}:
                seen[sid] = now  # the one they left: they were there until now
            st.setdefault("seen_since", now)
            for sid in [k for k, v in seen.items() if now - v > SEEN_KEEP_DAYS * 86400000]:
                del seen[sid]
        store.update_state(fn)

    def _answer_seen(self, s, sid, p):
        """The automatic to-do "read its answer" (pend.READ_ANSWER) is done
        once the user's keys are in the session's pane after the turn ended."""
        if not p.get("needs_auto") or p.get("needs_done") or q.working(s) or not self._user_in(s):
            return

        def fn(st):
            rec = st.get("pending", {}).get(sid)
            if rec:
                rec["needs_done"] = True
        store.update_state(fn)
        store.log(f"answer seen: {s.get('name')}")

    async def run_check(self, s, p):
        sid = s["sessionId"]
        try:
            proc = await asyncio.create_subprocess_shell(
                p["check"], cwd=p.get("cwd") or os.path.expanduser("~"),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=CHECK_TIMEOUT)
                rc, out = proc.returncode, out.decode("utf-8", "replace")
            except asyncio.TimeoutError:
                proc.kill()
                rc, out = -1, "timeout"
            now_ms = int(time.time() * 1000)

            def fn(st):
                rec = st.get("pending", {}).get(sid)
                if rec:
                    rec.update(last_check=now_ms, last_rc=rc, last_out=out[-300:])
            st = store.update_state(fn)
            store.log(f"check {s.get('name')}: exit {rc}")
            rec = st.get("pending", {}).get(sid)
            if rc == 0 and rec:
                await self.fire(s, rec, "check", out)
        except Exception as ex:
            store.log(f"check error {s.get('name')}: {ex!r}")
        finally:
            self.checking.discard(sid)
            self.wake.set()

    async def fire(self, s, p, reason, output=""):
        """Wake a pending session: type the wake-up message into its prompt.
        If that is not safe, put it into the user's queue instead."""
        msg = pend.wake_message(p, reason, output)
        pane = self.pane_of(s)
        if not pane:
            ok, why = False, f"no {self.bridge.label} pane"
        elif s["status"] != "idle":
            ok, why = False, ("the session waits on a question" if s["status"] == "waiting"
                              else f"the session is not at its prompt (status {s['status']})")
        else:
            ok, why = await self.bridge.send_prompt(pane, msg)
        text = "the check succeeded" if reason == "check" else (
            "the check did not succeed in time" if p.get("check") else "the time came")

        def fn(st):
            st.get("pending", {}).pop(s["sessionId"], None)
            st["sessions"].setdefault(s["key"], {})["woke"] = {
                "at": int(time.time() * 1000), "reason": text, "delivered": ok,
                "why_not": why, "stamp": s["statusSince"], "message": msg}
        store.update_state(fn)
        store.log(f"wake {s.get('name')}: {text}; " + ("message sent" if ok else f"not sent ({why})"))
        self.flash(f"Woke {s.get('name')}: {text}" + ("" if ok else f" (not sent: {why})"))

    async def retry_wakeups(self, state):
        """Type a wake-up that could not be typed (a draft, an open question,
        an unread screen) when its session is at an empty prompt again, in
        the same state. Not while the user is in that pane: they may type there."""
        for s in self.sessions:
            e = state["sessions"].get(s["key"], {})
            wk = e.get("woke")
            if (not wk or wk.get("delivered") or wk.get("stamp") != s["statusSince"]
                    or s["status"] != "idle" or e.get("dismissed_stamp") == s["statusSince"]
                    or self._user_in(s)):
                continue
            pane = self.pane_of(s)
            if not pane or await self.bridge.prompt_state(pane) != EMPTY:
                continue
            ok, _ = await self.bridge.send_prompt(pane, pend.late_wake_message(wk))
            if not ok:
                continue
            key = s["key"]

            def fn(st, key=key):
                rec = st["sessions"].get(key, {}).get("woke")
                if rec:
                    rec.update(delivered=True, delivered_at=int(time.time() * 1000))
            store.update_state(fn)
            store.log(f"wake {s.get('name')}: {wk.get('reason')}; message sent late "
                      f"(it could not be sent at {pend.fmt_ms(wk['at'])})")
            self.flash(f"Woke {s.get('name')} late: {wk.get('reason')}")

    # ---- selection and actions -------------------------------------------

    def rows(self):
        v = self.view or {}
        return [(sec, i) for sec, _ in SECTIONS for i in v.get(sec, [])]

    def selected(self):
        """The selected row, or else the top of NEED YOU, or else the oldest
        of YOUR TO-DOS (None, None when there is none of them)."""
        if self.sel:
            for sec, i in self.rows():
                if i["s"]["key"] == self.sel:
                    return sec, i
        v = self.view or {}
        for sec in ("need", "todo"):
            if v.get(sec):
                return sec, v[sec][0]
        return None, None

    def move(self, delta):
        """Select the next (delta 1) or previous (-1) row. The list wraps:
        down from the last row goes to the first, up from the first to the last."""
        rows = self.rows()
        if not rows:
            return
        sec, cur = self.selected()
        if cur is None:
            idx = 0 if delta > 0 else len(rows) - 1
        else:
            idx = next((n for n, (_, i) in enumerate(rows) if i is cur), 0)
            idx = (idx + delta) % len(rows)
        self.sel = rows[idx][1]["s"]["key"]

    async def go_selected(self):
        sec, item = self.selected()
        if not item:
            self.flash("Nothing selected: ↑ ↓ select a row.")
            return
        if sec == "terminals":
            await self.pick_folder(item)
            return
        s = item["s"]
        pane = self.pane_of(s)
        job = s.get("jobId") or s.get("id")
        if not pane and job:
            # A background session (/bg) has no pane: attach it in a new window (a new herdr tab).
            claude = shutil.which("claude") or "claude"
            self.sel = None
            await self.bridge.open_window(f"{claude} attach {job}")
            return
        if not pane:
            self.flash(f"{s.get('name')}: no {self.bridge.label} pane found")
            return
        self.sel = None  # after this, the highlight is on the top of the queue again
        if self.answer_watch:
            self.answer_watch.cancel()
            self.answer_watch = None
        before = await self._dialog_snapshot(s) if self.cfg["return_after_answer"] else None
        if await self.bridge.go(pane, zoom=self.cfg["zoom"]) and before:
            self.answer_watch = asyncio.ensure_future(
                self._guard("answer watch", self._return_after_answer(s, pane, *before)))

    async def _dialog_snapshot(self, s):
        """(status stamp, id of the last question) of a session that shows a
        dialog now, or None."""
        st = sessions_mod.status_of(s["pid"], s["sessionId"])
        if not st or st[0] != "waiting":
            return None
        qid, _ = await asyncio.get_event_loop().run_in_executor(
            None, sessions_mod.last_question, s["cwd"], s["sessionId"])
        return st, qid

    async def _return_after_answer(self, s, pane, start, before):
        """After Enter on a session that shows a dialog: when the user submits
        the answers to its question, bring the focus window back (config
        "return_after_answer"). Claude Code writes the question to the
        transcript only when the dialog closes, so the watch starts on every
        dialog and ends when the status changes: a new answered question
        brings the focus window back; Esc, "Chat about this" or a permission
        prompt leaves the focus where it is."""
        while True:
            await asyncio.sleep(ANSWER_POLL)
            st = sessions_mod.status_of(s["pid"], s["sessionId"])
            if st is None:
                return  # the session ended
            if st != start:
                break
        loop = asyncio.get_event_loop()
        for _ in range(10):  # the result can reach the transcript a moment later
            qid, outcome = await loop.run_in_executor(
                None, sessions_mod.last_question, s["cwd"], s["sessionId"])
            if qid != before and outcome:
                break
            await asyncio.sleep(0.2)
        else:
            return  # no new question: a permission prompt
        # Only when the user is still in that pane: never pull them out of another window or app.
        if outcome == "answered" and await self.bridge.has_focus(pane):
            await self.bridge.activate_own()
            self.flash(f"Back from {s.get('name')}: you answered its question.")
            store.log(f"answered {s.get('name')}: back to the focus window")

    async def go_top(self):
        self.sel = None
        await self.go_selected()

    def _free_terminal(self, item=None):
        """The selected free terminal, or the first one (key n)."""
        terms = (self.view or {}).get("terminals") or []
        item = item or (terms[0] if terms else None)
        if not item:
            self.flash(f"No free terminal: switch a pane to the {self.cfg['free_profile']} profile."
                       if self.bridge.profiles else "No free terminal: open a herdr pane at a shell prompt.")
        return item

    async def go_terminal(self, item=None):
        """Go to a free terminal without starting anything in it."""
        item = self._free_terminal(item)
        if not item:
            return
        self.sel = None
        if not await self.bridge.go(item["pane"], zoom=self.cfg["zoom"]):
            self.flash(f"The terminal in {item['path']} is gone.")

    async def pick_folder(self, item=None):
        """Enter on a free terminal, or n: choose the folder of a new Claude
        session in it. Typing searches the folders (see folders.search)."""
        item = self._free_terminal(item)
        if not item:
            return
        self.folders = await asyncio.get_event_loop().run_in_executor(
            None, folders.candidates, self.cfg["project_roots"])
        self.pick, self.mode, self.buf = {"term": item, "sel": 0}, "folders", ""
        self._search()

    def _search(self):
        """The matching folders, then (one row more, selected when nothing
        matches) the new folder that what the user typed would make."""
        self.matches = folders.search(self.folders, self.buf, short_path)
        self.new_folder = folders.new_folder(self.buf, self.cfg["project_roots"])
        self.pick["sel"] = 0

    def _creating(self):
        """True while the picker's selection is the new-folder row."""
        return bool(self.new_folder) and self.pick["sel"] == len(self.matches)

    async def choose_start(self, term, path):
        """A folder is picked. Where Claude Code ran before, ask how to start
        (START_KINDS: a new session, --continue or --resume); else start a
        new session at once."""
        past = await asyncio.get_event_loop().run_in_executor(None, folders.past_sessions, path)
        if not past[0]:
            self.mode = "main"
            await self.start_session(term, path)
            return
        self.pick.update(path=path, past=past, kind=0)
        self.mode = "start"

    async def start_session(self, term, path, create=False, flags=""):
        """Type `cd <folder> && claude` into a free terminal (with create,
        `mkdir -p <folder> && ` first; flags such as " --continue" after the
        command), give it the session profile (iTerm2), and go there. Only
        into a pane that is still at a bare shell; Ctrl-U first clears
        anything typed at its prompt."""
        info = {sid: (profile, job) for sid, _, profile, _, job in await self.bridge.pane_info()}
        profile, job = info.get(term["pane"], (None, None))
        where = shlex.quote(path)
        mkdir = f"mkdir -p {where} && " if create else ""
        if (job or "").lstrip("-") not in SHELLS or not await self.bridge.send_text(
                term["pane"], f"\x15{mkdir}cd {where} && {self.cfg['new_session_command']}{flags}\r"):
            self.flash(f"The terminal {term['tty']} is not at a shell prompt any more: nothing started.")
            return
        if self.bridge.profiles and profile == self.cfg["free_profile"]:
            await self.bridge.set_profile(term["pane"], self.cfg["session_profile"])
        store.log(f"new session{flags} in {'a new folder ' if create else ''}{short_path(path)} ({term['tty']})")
        self.sel = None
        await self.bridge.go(term["pane"], zoom=self.cfg["zoom"])

    def next_theme(self):
        """Key t: the next theme of look.THEMES, kept in the config."""
        names = list(look.THEMES)
        now = self.cfg["theme"]
        name = names[(names.index(now) + 1) % len(names)] if now in names else names[0]
        self.cfg["theme"] = name
        self.pal = look.palette(self.appearance, name)
        store.ensure()
        store._write(store.CONFIG, dict(store._read(store.CONFIG, {}), theme=name))
        self.flash(f"Theme: {name}. t switches to the next one ({', '.join(names)}).")

    def _update_selected(self, fn, done_text):
        sec, item = self.selected()
        if not item:
            return
        s = item["s"]
        store.update_state(lambda st: fn(st, st["sessions"].setdefault(s["key"], {}), s))
        self.flash(done_text.format(name=s.get("name")))
        store.log("key: " + done_text.format(name=s.get("name")))

    def snooze(self, until, note):
        def fn(st, e, s):
            e.update(snooze_until=int(until.timestamp() * 1000), snooze_stamp=s["statusSince"],
                     snooze_note=note, skips=e.get("skips", 0) + 1)
        self._update_selected(fn, "{name} snoozed until " + until.strftime("%a %H:%M") + ".")

    def set_priority(self, value):
        self._update_selected(lambda st, e, s: e.__setitem__("manual_priority", value or None),
                              "{name}: priority " + str(value or "back to automatic") + ".")

    def dismiss(self):
        sec, item = self.selected()
        if item and item["s"]["status"] == "waiting":
            self.flash(f"{item['s'].get('name')} asks you a question. Answer it, or snooze it (s).")
            return
        if item and item.get("needs"):  # the user did their part; the session still waits
            sid, name = item["s"]["sessionId"], item["s"].get("name")

            def fn(st):
                rec = st.get("pending", {}).get(sid)
                if rec:
                    rec["needs_done"] = True
            store.update_state(fn)
            text = f"{name}: your part is done. It still waits for: {item.get('why')}."
            self.flash(text)
            store.log("key: " + text)
            return
        self._update_selected(lambda st, e, s: e.__setitem__("dismissed_stamp", s["statusSince"]),
                              "{name} marked done. It comes back when it changes.")

    def unwait(self):
        def fn(st, e, s):
            for k in ("snooze_until", "snooze_stamp", "snooze_note", "dismissed_stamp", "woke"):
                e.pop(k, None)
            st.get("pending", {}).pop(s["sessionId"], None)
        self._update_selected(fn, "{name}: wait cleared, back in the queue.")

    def rescore(self):
        sec, item = self.selected()
        if not item:
            return
        key = item["s"]["key"]
        if not self.engine:  # the engine (service) picks the request up on its next poll
            store.update_state(lambda st: st.setdefault("rescore", []).append(key))
            self.flash("Rescore requested.")
        elif key not in self.scoring:
            self.scoring.add(key)
            asyncio.ensure_future(self._score(item["s"]))
            self.flash("Rescoring...")

    def restore_plan(self):
        """(windows, skipped) of the previous iTerm2 run that are closed now,
        or None: nothing to open, or the user already answered for that layout."""
        snap = self.prev_layout
        if not self.polled or not snap or self.restore_handled == snap["at"]:
            return None
        windows, skipped = restore.plan(snap, {s["sessionId"] for s in self.sessions}, focus_running=True)
        return (windows, skipped) if windows else None

    def _restore_answered(self):
        at = self.prev_layout["at"]
        store.update_state(lambda st: st.__setitem__("restore_handled", at))
        self.restore_handled = at

    async def show_focus_window(self):
        """Hotkey: bring the focus window to the front, or open one."""
        if not self.headless:
            await self.bridge.activate_own()
            return
        await show_focus(self.bridge)

    def _set_focused(self, focused):
        if focused != self.focused:
            self.focused = focused
            self.wake.set()

    def flash(self, text, seconds=6):
        self.msg, self.msg_until = text, time.time() + seconds
        self.wake.set()

    # ---- keys -------------------------------------------------------------

    async def on_key(self, key):
        sec, item = self.selected()
        if self.mode == "main":
            if key in ("\r", "\n"):
                await self.go_selected()
            elif key in ("\x1b[A", "\x1bOA", "k"):
                self.move(-1)
            elif key in ("\x1b[B", "\x1bOB", "j"):
                self.move(1)
            elif key == "\x1b":
                self.sel = None
            elif key == "n":
                await self.pick_folder()
            elif key == "t":
                self.next_theme()
            elif sec == "terminals" and key in ("s", "p", "d", "u", "r"):
                self.flash("A free terminal: Enter starts a new session in it.")
            elif key == "s" and item:
                if sec == "working":
                    self.flash("It is working now; nothing to snooze.")
                else:
                    self.mode = "snooze"
            elif key == "p" and item:
                self.mode, self.buf = "priority", ""
            elif key == "d" and item:
                self.dismiss()
            elif key == "u" and item:
                self.unwait()
            elif key == "r":
                self.rescore()
            elif key == "R":
                p = self.restore_plan()
                if p:
                    self.mode, self.restore_lines = "restore", restore.describe(self.prev_layout, *p)
                elif not self.bridge.restores:
                    self.flash(f"{self.bridge.label} keeps its own layout and sessions: nothing to restore here.")
                else:
                    self.flash("Nothing to restore: no session of the last iTerm2 run is closed.")
            elif key == "q":
                raise SystemExit
        elif self.mode == "restore":
            if key == "y":
                p = self.restore_plan()
                self._restore_answered()
                self.mode = "main"
                if p:
                    asyncio.ensure_future(self._guard("restore", restore.run(self.bridge.conn, p[0], self.flash)))
            elif key == "n":
                self._restore_answered()
                self.mode = "main"
                self.flash("This layout will not be offered again (claude-focus restore still opens it).")
            elif key.startswith("\x1b"):
                self.mode = "main"
        elif self.mode == "snooze":
            now = dt.datetime.now()
            presets = {k: (label, when) for k, label, when in q.snooze_presets(now)}
            if key in presets:
                label, when = presets[key]
                self.snooze(when, f"snoozed ({label})")
                self.mode = "main"
            elif key == "t":
                self.mode, self.buf = "snooze_time", ""
            elif key.startswith("\x1b"):
                self.mode = "main"
        elif self.mode in ("snooze_time", "priority"):
            if key.startswith("\x1b"):
                self.mode = "main"
            elif key in ("\r", "\n"):
                if self.mode == "priority":
                    try:
                        self.set_priority(max(0, min(100, int(self.buf or 0))))
                    except ValueError:
                        self.flash("Type a number from 1 to 100 (0 = automatic).")
                else:
                    when = q.parse_when(self.buf, dt.datetime.now())
                    if when and when > dt.datetime.now():
                        self.snooze(when, f"snoozed until {self.buf}")
                    else:
                        self.flash("Use HH:MM, +2h, +30m, +1d or YYYY-MM-DD HH:MM.")
                self.mode = "main"
            elif key in ("\x7f", "\b"):
                self.buf = self.buf[:-1]
            elif key.isprintable():
                self.buf += key
        elif self.mode == "folders":
            # Every letter goes into the search, n and q too; only Esc leaves.
            term, n = self.pick["term"], len(self.matches) + bool(self.new_folder)
            if key == "\x1b":
                self.mode = "main"
            elif key in ("\x1b[A", "\x1bOA", "\x1b[B", "\x1bOB") and n:
                self.pick["sel"] = (self.pick["sel"] + (-1 if key[-1] == "A" else 1)) % n
            elif key in ("\r", "\n") and n:
                if self._creating():
                    self.mode = "main"
                    await self.start_session(term, self.new_folder, create=True)
                else:
                    await self.choose_start(term, self.matches[self.pick["sel"]][0])
            elif key == "\t":
                self.mode = "main"
                await self.go_terminal(term)
            elif key in ("\x7f", "\b", "\x15"):
                self.buf = "" if key == "\x15" else self.buf[:-1]
                self._search()
            elif key.isprintable():
                self.buf += key
                self._search()
        elif self.mode == "start":
            term, letters = self.pick["term"], [k for k, _, _ in START_KINDS]
            if key == "\x1b":
                self.mode = "folders"  # back to the folder list, with the search
            elif key in ("\x1b[A", "\x1bOA", "\x1b[B", "\x1bOB"):
                self.pick["kind"] = (self.pick["kind"] + (-1 if key[-1] == "A" else 1)) % len(START_KINDS)
            elif key in ("\r", "\n") or key in letters:
                kind = START_KINDS[letters.index(key) if key in letters else self.pick["kind"]]
                self.mode = "main"
                await self.start_session(term, self.pick["path"], flags=kind[2])
            elif key == "\t":
                self.mode = "main"
                await self.go_terminal(term)
        self.wake.set()

    # ---- render -----------------------------------------------------------
    #
    # One grid for every section, so the eye finds each column in the same
    # place: a marker (▌ on the selected row), a lead (the priority as a
    # number and a bar, the wake time, or a sign), an age (how long it
    # waits, works or sleeps), the name, a kind and the text. Color marks
    # what matters: the section, a high priority, and alerts (a wake-up that
    # was not typed, a duplicate, no pane). The rest is grey.

    def _cols(self, w):
        # The folder column is as wide as the longest folder on the screen, and
        # only on a wide screen; on a narrow one WORKING shows it as its text.
        wide = w >= 110
        paths = [self._path(sec, i) for sec, _ in SECTIONS for i in (self.view or {}).get(sec) or []] if wide else []
        return {"lead": 13, "age": 9, "name": 28 if wide else 20,
                "path": min(PATH_CELLS, max(map(len, paths), default=0)), "kind": 9 if w >= 100 else 0}

    @staticmethod
    def _path(sec, item):
        """The folder of a row: the session's cwd, or a free terminal's path."""
        if sec == "terminals":
            return item["path"]
        return short_path(item["s"]["cwd"]) if item["s"].get("cwd") else ""

    # How bright a row's name and text are: what needs the user stands out, what
    # waits or works is softer, and FREE is grey.
    TONE = {"need": ("text", "text"), "todo": ("text", "text"), "pending": ("soft", "soft"),
            "working": ("soft", "soft"), "free": ("dim", "dim"), "terminals": ("text", "dim")}

    def _row_parts(self, sec, item, path_col=False):
        """(lead, age, kind, tags, text) of one row; the lead, tags and text can carry color.
        path_col: the row has a folder column, so the text need not name the folder."""
        P, s, sc = self.pal, item["s"], item["fresh"] or {}
        acc, dim, alert = P[sec], fg(P["dim"]), fg(P["alert"])
        now_ms = time.time() * 1000
        since = fmt_wait(max(0, now_ms - s["statusSince"]) / 3.6e6) if "statusSince" in s else ""
        tags, text = [], ""
        if sec == "terminals":
            where = "" if path_col or item["path"] == item["s"]["name"] else item["path"] + "  "
            return f"{fg(acc)}❯{R}", "", item["job"], [], where + item["tty"]
        if sec == "need":
            eff = item["eff"]
            level = P["high"] if eff >= HIGH else P["mid"] if eff >= MID else P["low"]
            lead = f"{fg(level)}{B}{eff:>3.0f}{R} " + look.bar(eff, PRIORITY_CELLS, level, P["track"])
            age, kind = fmt_wait(item["waited_h"]), KIND_LABEL.get(sc.get("kind"), sc.get("kind") or "")
            text = sc.get("ask") or f"{dim}scoring...{R}"
            if item.get("woke"):
                wk = item["woke"]
                tags.append(f"{alert}{B}WAKE-UP{R}{alert} {wk['reason']}, not sent: {wk['why_not']}{R}")
            if sc.get("deadline"):
                tags.append(f"{alert}due {sc['deadline']}{R}")
            if sc.get("effort_min") is not None:
                text += f"{dim}  ~{sc['effort_min']} min{R}"
        elif sec == "todo":
            lead, age, kind = f"{fg(acc)}☐{R}", fmt_wait(item["waited_h"]), "your task"
            text = item["needs"] + f"{dim}  ·  then it waits for: {item.get('why', '')} "\
                                   f"(wakes {fmt_when(item['wake'])}){R}"
        elif sec == "pending":
            p, e = item.get("pending"), item["e"]
            left = item["wake"] - now_ms
            lead = f"{fg(acc)}{fmt_when(item['wake'])}{R}"
            age = f"in {fmt_wait(left / 3.6e6)}" if left > 60000 else "now"
            snoozed = e.get("snooze_stamp") == s["statusSince"] and (e.get("snooze_until") or 0) > now_ms
            kind = "snoozed" if snoozed else "check" if p and p.get("check") else "wait" if p else "later"
            text = item.get("why", "")
        elif sec == "working":
            shells = s["status"] == "shell"
            lead, age = f"{fg(acc)}{'◌' if shells else '●'}{R}", since
            kind = "shells" if shells else "busy"
            text = "" if path_col else short_path(s.get("cwd"))
        else:  # free
            done = item["e"].get("dismissed_stamp") == s["statusSince"]
            lead, age, kind = f"{fg(acc)}○{R}", since, "done" if done else "nothing"
            text = sc.get("ask") or item.get("why", "")
        if s.get("dup"):
            tags.append(f"{alert}{B}DUPLICATE ×{s['dup']}{R}")
        if item["e"].get("skips"):
            tags.append(f"{alert}snoozed {item['e']['skips']}×{R}")
        if not self.pane_of(s):
            tags.append(f"{dim}background session{R}" if s.get("jobId") or s.get("id")
                        else f"{alert}no pane{R}")
        return lead, age, kind, tags, text

    def row(self, sec, item, w, selected=False):
        P, cols = self.pal, self._cols(w)
        lead, age, kind, tags, text = self._row_parts(sec, item, path_col=bool(cols["path"]))
        name_tone, text_tone = ("text", "text") if selected else self.TONE[sec]
        name = look.cut(item["s"].get("name") or "?", cols["name"])
        name = f"{fg(P[name_tone])}{B if name_tone != 'dim' else ''}{name}{R}"
        mark = f"{fg(P[sec])}▌{R} " if selected else "  "
        line = (mark + look.pad(lead, cols["lead"]) + look.pad(f"{fg(P['dim'])}{age}{R}", cols["age"], right=True)
                + "  " + look.pad(name, cols["name"]) + "  ")
        if cols["path"]:
            path = look.cut_left(self._path(sec, item), cols["path"])
            line += look.pad(f"{fg(P['dim'])}{path}{R}", cols["path"]) + "  "
        if cols["kind"]:
            line += look.pad(f"{fg(mix(P[sec], P['bg'], 0.8))}{look.cut(kind, cols['kind'])}{R}", cols["kind"]) + " "
        line += "".join(t + "  " for t in tags) + fg(P[text_tone]) + text + R
        return self._on(line, P[sec], w) if selected else fit(line, w)

    def _on(self, line, color, w):
        """The selected row: on a tint of its section's color; in a theme with
        a selection bar (Norton Commander), all in the bar's colors."""
        P = self.pal
        if P.get("select"):
            text, back = P["select"]
            return look.on(fg(text) + look.ANSI.sub("", line), back, w)
        return look.on(line, mix(color, P["bg"], 0.16), w)

    def section_title(self, sec, title, n, w):
        P, acc = self.pal, self.pal[sec]
        count = f" {fg(acc)}{n}{R}" if n != "" else ""  # "": a title without a count (the start step)
        if P.get("frame"):  # a divider of the frame: ╟─ NEED YOU 3 ───╢ (see _framed)
            head = f"{fg(P['frame'])}─ {fg(acc)}{B}{title}{R}{count} "
            return head + fg(P["frame"]) + "─" * max(0, w - width(head)) + R
        head = f" {bg(mix(acc, P['bg'], 0.24))}{fg(acc)}{B} {title} {R}{count} "
        return head + fg(P["faint"]) + "─" * max(0, w - width(head)) + R

    def _framed(self, line, inner):
        """A line of the list inside the theme's frame. A section title (it
        starts with ─) joins the frame as a divider."""
        fc = fg(self.pal["frame"])
        side = "╟╢" if look.ANSI.sub("", line).startswith("─") else "║║"
        return f"{fc}{side[0]}{R}" + look.pad(fit(line, inner), inner) + f"{fc}{side[1]}{R}"

    def _title_line(self, v, w, now):
        P = self.pal
        counts = [(sec, len(v.get(sec, []))) for sec, _ in SECTIONS]
        extra = []
        if self.scoring_now():
            extra.append(f"scoring {len(self.scoring_now())}")
        if not self.engine:
            extra.append("service" if self.remote.get("mode") == "service" else "engine in another window")
        clock = now.strftime("%H:%M")
        if self.focused:  # a full-width green bar: this window gets the keys
            right = "   ".join([f"{n} {COUNT_LABEL[sec]}" for sec, n in counts] + extra + [clock])
            gap = " " * max(1, w - 14 - len(right))
            bar = fg(P["bar"][0]) + bg(P["bar"][1]) + B if P.get("bar") else FOCUS_BAR
            return f"{bar} CLAUDE FOCUS{gap}{right} {R}"
        parts = [f"{fg(P[sec]) if n else fg(P['faint'])}{B}{n}{R} {fg(P['dim'])}{COUNT_LABEL[sec]}{R}"
                 for sec, n in counts]
        right = "   ".join(parts + [f"{fg(P['dim'])}{x}{R}" for x in extra] + [f"{fg(P['text'])}{clock}{R}"])
        left = f" {fg(P['text'])}{B}CLAUDE FOCUS{R}"
        return left + " " * max(1, w - width(left) - width(right) - 1) + right + " "

    def _folder_lines(self, w, room):
        """The folder picker: what the user typed, then the matching folders
        with the matched letters bright, and when a session last ran there.
        The list scrolls to keep the selected folder in view. Below it, the
        new folder that what the user typed would make (always in view)."""
        P, acc, term = self.pal, self.pal["terminals"], self.pick["term"]
        dim = fg(P["dim"])
        lines = [self.section_title("terminals", f"NEW SESSION IN {term['tty']}", len(self.matches), w)]
        hint = "" if self.buf else f"   {dim}type to search: a folder name, its first letters, or a new name{R}"
        lines.append(f"  {fg(acc)}{B}›{R} {fg(P['text'])}{self.buf}{R}{fg(acc)}█{R}{hint}")
        new = []
        if self.new_folder:
            on = self._creating()
            line = (f"{fg(acc)}▌{R} " if on else "  ") + f"{fg(acc)}{B}+ new folder{R} " \
                f"{fg(P['text'] if on else P['soft'])}{look.cut_left(short_path(self.new_folder), w - 20)}{R}"
            new = [self._on(line, acc, w) if on else line]
        if not self.matches:
            return lines + [f"  {dim}No folder matches.{R}"] + new
        sel = min(self.pick["sel"], len(self.matches) - 1)
        show = max(1, room - len(lines) - len(new))
        start = sel - show + 1 if sel >= show else 0
        shown = self.matches[start:start + show]
        texts = [short_path(p) for p, _, _ in shown]
        pw = min(max(map(len, texts)), w - 20)
        now = time.time()
        for n, ((_, when, used), text) in enumerate(zip(shown, texts)):
            selected = start + n == self.pick["sel"]
            text = look.cut_left(text, pw)
            hit, tone = folders.marks(text, self.buf), fg(P["text"] if selected else P["soft"])
            body = "".join(f"{fg(acc)}{B}{c}{R}" if i in hit else f"{tone}{c}{R}" for i, c in enumerate(text))
            age = ago(when) if used else "no recent session"
            line = (f"{fg(acc)}▌{R} " if selected else "  ") + look.pad(body, pw) + f"  {dim}{age}{R}"
            lines.append(self._on(line, acc, w) if selected else line)
        return lines + new

    def _start_lines(self, w):
        """The step after the folder picker, in a folder where Claude Code ran
        before: a new session, the last one continued, or an earlier one
        resumed from Claude Code's own list. Each row shows its command."""
        P, acc, term, path = self.pal, self.pal["terminals"], self.pick["term"], self.pick["path"]
        dim = fg(P["dim"])
        n, last = self.pick["past"]
        about = f"{n} session{'s' if n != 1 else ''} here, the last {ago(last)}"
        running = sum(1 for s in self.sessions if s.get("cwd") == path)
        if running:
            about += f"; {running} of them run{'s' if running == 1 else ''} now"
        lines = [self.section_title("terminals", f"NEW SESSION IN {term['tty']}", "", w),
                 f"  {fg(P['text'])}{B}{look.cut_left(short_path(path), w - 4)}{R}   {dim}{about}{R}", ""]
        lw = max(len(label) for _, label, _ in START_KINDS)  # the commands in one column
        for i, (key, label, flags) in enumerate(START_KINDS):
            on = i == self.pick["kind"]
            line = ((f"{fg(acc)}▌{R} " if on else "  ") + f"{fg(acc)}{B}{key}{R}  "
                    f"{fg(P['text'] if on else P['soft'])}{look.pad(label, lw)}{R}"
                    f"   {dim}{self.cfg['new_session_command']}{flags}{R}")
            lines.append(self._on(line, acc, w) if on else line)
        return lines

    def render(self):
        cols, rows_n = shutil.get_terminal_size((100, 40))
        w = max(50, cols - 1)
        P = self.pal
        v = self.view or {"need": [], "todo": [], "working": [], "pending": [], "free": [], "terminals": []}
        now = dt.datetime.now()
        top_lines = [self._title_line(v, w, now)]
        if self.scorer_error():
            top_lines.append(f"  {fg(P['alert'])}Scorer error: {self.scorer_error()}{R}")
        plan = self.restore_plan() if self.mode == "main" else None
        if plan:
            n = sum(len(x["panes"]) for x in plan[0])
            when = fmt_when(self.prev_layout["at"])
            top_lines.append(f"  {fg(P['pending'])}{B}{n} window{'s' if n != 1 else ''} from before the iTerm2 "
                             f"restart {'are' if n != 1 else 'is'} closed (layout saved {when}). "
                             f"Press R to restore them.{R}")
        frame = P.get("frame")  # a theme that frames the list (Norton Commander): two columns less inside
        inner = w - 2 if frame else w
        top_lines.append(f"{fg(frame)}╔{'═' * inner}╗{R}" if frame else "")

        # The keys are the last line of the screen, the flash just above the rule.
        bottom_lines = [f"  {fg(P['working'])}{self.msg}{R}" if self.msg and time.time() < self.msg_until else "",
                        f"{fg(frame)}╚{'═' * inner}╝{R}" if frame else fg(P["faint"]) + "─" * w + R]
        if self.mode == "restore" and self.restore_lines:
            bottom_lines += [f"  {fg(P['pending'])}{B}{self.restore_lines[0]}{R}"]
            bottom_lines += [f"  {fg(P['text'])}{line}{R}" for line in self.restore_lines[1:]] + [""]
        bottom_lines.append(self._keys_line(now))

        room = max(3, rows_n - len(top_lines) - len(bottom_lines))
        # The rows keep their places: the selection changes only the highlight.
        sec, item = self.selected()
        list_lines, sel_line = [], None
        if self.mode == "folders":
            list_lines = self._folder_lines(inner, room)
        elif self.mode == "start":
            list_lines = self._start_lines(inner)
        elif not self.rows():
            list_lines += [f"  {fg(P['dim'])}No Claude sessions and no free terminal.{R}"]
        elif not v.get("need") and not v.get("todo"):
            list_lines += [f"  {fg(P['terminals'])}{B}✓ Nothing needs you now.{R}"]
        for key, title in SECTIONS if self.mode not in ("folders", "start") else ():
            if not v.get(key):
                continue
            if list_lines:
                list_lines.append("")
            list_lines.append(self.section_title(key, title, len(v[key]), inner))
            for i in v[key]:
                if i is item:
                    sel_line = len(list_lines)
                list_lines.append(self.row(key, i, inner, selected=i is item))
        start = 0
        if len(list_lines) > room and sel_line is not None and sel_line >= room - 1:
            start = min(sel_line - room + 2, len(list_lines) - room)
        shown = list_lines[start:start + room]
        if start + room < len(list_lines):
            shown[-1] = f"  {fg(P['dim'])}… {len(list_lines) - start - room + 1} more lines (↓){R}"
        shown += [""] * (room - len(shown))  # a short list: blank lines push the keys down
        if frame:
            shown = [self._framed(x, inner) for x in shown]
            bottom_lines[0] = self._framed(bottom_lines[0], inner)
        lines = [fit(x, w) for x in top_lines + shown + bottom_lines]
        if P.get("paint"):  # every line on the theme's background, the keys on theirs; \033[K fills the rest
            lines = [look.on(x, P["keys_bg"] if n == len(lines) - 1 else P["bg"], w)[:-len(R)]
                     for n, x in enumerate(lines)]
        frame = "\n".join(lines)
        if frame == self.last_frame:
            return
        self.last_frame = frame
        # Synchronized update + overwrite in place (no full clear): no flicker.
        body = "\033[K\n".join(lines)
        sys.stdout.write("\033[?2026h\033[H" + body + "\033[K\033[J" + R + "\033[?2026l")
        sys.stdout.flush()

    def _key(self, cap, label):
        P = self.pal
        if P.get("keys"):  # a key bar: the key, then its label in a field of its own
            cap_fg, cap_bg, label_fg, label_bg = P["keys"]
            return f"{bg(cap_bg)}{fg(cap_fg)}{B}{cap}{R}{bg(label_bg)}{fg(label_fg)} {label} {R}"
        return f"{bg(P['key'])}{fg(P['text'])}{B} {cap} {R} {fg(P['dim'])}{label}{R}"

    def _keys_line(self, now):
        P = self.pal
        if self.mode == "restore":
            return "  " + "   ".join([self._key("y", "open them"), self._key("n", "no, do not offer this layout again"),
                                     self._key("esc", "back")])
        if self.mode == "snooze":
            opts = [self._key(k, label) for k, label, _ in q.snooze_presets(now)]
            return (f"  {fg(P['pending'])}{B}Snooze until{R}   " + "   ".join(
                opts + [self._key("t", "type a time"), self._key("esc", "cancel")]))
        if self.mode == "snooze_time":
            return (f"  {fg(P['pending'])}{B}Snooze until{R} {fg(P['dim'])}(HH:MM, +2h, +1d, "
                    f"YYYY-MM-DD HH:MM){R}  {fg(P['text'])}{self.buf}█{R}")
        if self.mode == "priority":
            return f"  {fg(P['need'])}{B}Priority{R} {fg(P['dim'])}1-100 (0 = automatic){R}  {fg(P['text'])}{self.buf}█{R}"
        if self.mode == "start":
            return "  " + "   ".join([self._key("⏎", "start"), self._key("↑↓", "select"),
                                     self._key("⇥", "just go to the terminal"), self._key("esc", "back to the folders")])
        if self.mode == "folders":
            match = self.matches[self.pick["sel"]] if self.pick["sel"] < len(self.matches) else None
            what = (f"make it, cd there and run {self.cfg['new_session_command']}" if self._creating() else
                    "start there: new, continue or resume" if match and match[2] else
                    f"cd there and run {self.cfg['new_session_command']}")
            return "  " + "   ".join([self._key("⏎", what),
                                     self._key("↑↓", "select"), self._key("⇥", "just go to the terminal"),
                                     self._key("esc", "cancel")])
        keys = [("⏎", "go"), ("↑↓", "select"), ("esc", "top"), ("n", "new session"), ("s", "snooze"),
                ("u", "unwait"), ("p", "priority"), ("d", "done"), ("r", "rescore"), ("t", "theme"),
                ("q", "quit")]
        if self.cfg.get("hotkeys"):
            hk = self.cfg["hotkey_go"].replace("ctrl", "⌃").replace("option", "⌥").replace("+", "").upper()
            keys.insert(3, (hk, "go to top"))
        wide = "  " + "   ".join(self._key(c, label) for c, label in keys)
        if width(wide) <= shutil.get_terminal_size((100, 40)).columns - 1:
            return wide
        if P.get("keys"):  # the key bar keeps its fields, with one-word labels
            return " " + " ".join(self._key(c, label.split()[-1]) for c, label in keys)
        return "  " + "  ".join(f"{fg(P['text'])}{B}{c}{R} {fg(P['dim'])}{label.split()[-1]}{R}" for c, label in keys)

    # ---- loops ------------------------------------------------------------

    async def poll_loop(self):
        while True:
            try:
                await self.poll()
            except Exception as ex:  # keep the screen alive; details in the log
                store.log(f"poll error: {ex!r}")
            self.wake.set()
            self.poll_now.clear()
            try:
                await asyncio.wait_for(self.poll_now.wait(), timeout=self.cfg["poll_seconds"])
                await asyncio.sleep(EVENT_PAUSE)
            except asyncio.TimeoutError:
                pass

    async def render_loop(self):
        while True:
            try:
                self.render()
            except Exception as ex:
                store.log(f"render error: {ex!r}")
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    async def _guard(self, name, coro):
        try:
            await coro
        except Exception as ex:
            store.log(f"{name} stopped: {ex!r}")
            self.flash(f"{name} stopped: {ex!r} (see ~/.claude-focus/focus.log)", 60)

    async def _watch_terminal(self):
        """The service: when detection picks the other terminal now (the
        sessions run there, see terminal.detect), exit, and launchd starts
        the service again on that one."""
        while True:
            await asyncio.sleep(TERMINAL_CHECK)
            kind = await asyncio.get_event_loop().run_in_executor(
                None, lambda: terminal.detect(current=self.bridge.name))
            if kind and kind != self.bridge.name:
                store.log(f"service: the sessions run in {kind} now, not {self.bridge.name}: restarting")
                raise SystemExit(0)

    async def run_headless(self):
        store.ensure()
        with open(pend.SERVICE_PID, "w") as f:
            f.write(str(os.getpid()))
        await self.bridge.start()
        store.log(f"service connected to {self.bridge.label} (pid {os.getpid()})")
        asyncio.ensure_future(self._guard("terminal watch", self._watch_terminal()))
        asyncio.ensure_future(self._guard("events", self.bridge.watch_events(self.poll_now.set)))
        await self.poll_loop()

    async def run(self):
        store.ensure()
        own = self.bridge.own_pane()
        with open(TUI_FILE, "w") as f:
            json.dump({"pid": os.getpid(), "session": own}, f)
        await self.bridge.start()
        self.focused = await self.bridge.has_focus(own)
        asyncio.ensure_future(self._guard("focus watch", self.bridge.watch_focus(own, self._set_focused)))
        asyncio.ensure_future(self._guard("events", self.bridge.watch_events(self.poll_now.set)))
        loop = asyncio.get_event_loop()
        if self.bridge.restores:
            self.prev_layout = await loop.run_in_executor(
                None, lambda: restore.previous_layout(restore.read_history(), restore.iterm_run()))
        keyq = asyncio.Queue()
        fd = sys.stdin.fileno()
        loop.add_reader(fd, lambda: keyq.put_nowait(os.read(fd, 64).decode("utf-8", "ignore")))
        asyncio.ensure_future(self.poll_loop())
        asyncio.ensure_future(self.render_loop())
        self._start_hotkeys()
        while True:
            data = await keyq.get()
            for key in tokens(data):
                try:
                    await self.on_key(key)
                except SystemExit:
                    return


async def show_focus(bridge):
    """Bring the open focus screen to the front, or open one."""
    pane = restore.focus_pane()
    if not (pane and await bridge.go(pane, zoom=False)):
        await bridge.open_window(BIN)


def main():
    kind = terminal.detect()
    if kind is None:
        sys.exit(terminal.NOT_RUNNING)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    sys.stdout.write("\033[?1049h\033[?25l\033]0;claude-focus\007\033[2J")
    try:
        ttymod.setcbreak(fd)
        terminal.run(lambda bridge: App(bridge).run(), kind)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()
