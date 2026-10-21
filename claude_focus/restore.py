"""Crash recovery. The engine appends a snapshot of the layout (each iTerm
window, and the Claude session in each of its panes) to layouts.jsonl when
the layout changes. After a crash, a reboot or any restart of iTerm2,
`claude-focus restore` (or R on the focus screen) opens the last layout of
the previous iTerm2 run again: one window per window, on the same display,
full screen as before, with `claude --resume <id>` typed into each shell."""
import asyncio
import datetime as dt
import json
import os
import shlex
import subprocess
import time

from . import pending as pend
from . import sessions as sessions_mod
from . import store

HISTORY = os.path.join(store.DIR, "layouts.jsonl")
TUI_FILE = os.path.join(store.DIR, "tui.json")
KEEP_DAYS = 3
PRUNE_BYTES = 1_000_000
QUIT_SPAN_MS = 120_000    # when iTerm2 quits, its panes close one by one
OPEN_PAUSE = 2.0          # seconds between windows: full-screen animation, MCP servers
REARM_SECONDS = 90        # how long a restore waits for a resumed session to start
# Flags of the original `claude` command that a resume keeps. The others
# (-r, -c, -n, --session-id, -p ...) only say how that session started.
KEEP_FLAGS = {"--dangerously-skip-permissions": 0, "--allow-dangerously-skip-permissions": 0,
              "--permission-mode": 1, "--model": 1, "--effort": 1, "--add-dir": 1}
PENDING_KEYS = ("until", "check", "every_min", "note", "needs", "needs_done")
SAVE_VALID_MS = 30 * 60 * 1000  # a save for a restart counts when the run ends within 30 min
AUTO_RESTORE_DELAY = 8          # seconds: let iTerm2 open its own first window first
STOP_SECONDS = 30               # how long `reboot` waits for a working session to stop
RESTORE_PREFIX = "[claude-focus restore]"
SAFE_STEP = (" Before any irreversible or outward-facing step (send, deploy, publish, pay, "
             "delete), ask {user}, unless they already approved exactly that step.")
# The first message of a resumed session, by its status when the layout was
# saved ({user} and {User}: store.say).
RESUME_MESSAGES = {
    "busy": (f"{RESTORE_PREFIX} {{User}} restarted the computer, and this session was stopped while "
             "it worked. Check the current state now and continue the task you were doing." + SAFE_STEP),
    "shell": (f"{RESTORE_PREFIX} {{User}} restarted the computer, and the background shells of this "
              "session ended. Check what they did, start again what must still run, and continue "
              "the task you were doing." + SAFE_STEP),
    "waiting": (f"{RESTORE_PREFIX} {{User}} restarted the computer while you waited for their answer, "
                "so your open question (or permission prompt) was closed. Show it to them again now: "
                "ask the same question again, or retry the step that needed their permission."),
}


# ---- recording (the engine) ------------------------------------------------

def iterm_run():
    """The start time (Unix s) of the running iTerm2. It names the current
    run: a snapshot of another run is one that a restore can open."""
    try:
        out = subprocess.run(["ps", "-axo", "lstart=,comm="], capture_output=True, text=True,
                             env=dict(os.environ, LC_ALL="C"), timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if line.rstrip().endswith("/MacOS/iTerm2"):
            try:
                return int(dt.datetime.strptime(" ".join(line.split()[:5]),
                                                "%a %b %d %H:%M:%S %Y").timestamp())
            except ValueError:
                return None
    return None


def keep_flags(args):
    """The flags of a `claude ...` command line that a resume keeps."""
    words, out, i = args.split(), [], 1
    while i < len(words):
        flag = words[i].split("=")[0]
        n = 0 if "=" in words[i] else KEEP_FLAGS.get(flag, 0)
        if flag in KEEP_FLAGS:
            out += words[i:i + 1 + n]
        i += 1 + n
    return out


_procs = {}  # pid -> (tty, command line)


def _proc_info(pids):
    todo = [p for p in pids if p not in _procs]
    if todo:
        try:
            out = subprocess.run(["ps", "-o", "pid=,tty=,args=", "-p", ",".join(map(str, todo))],
                                 capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        for line in out.splitlines():
            parts = line.split(None, 2)
            if len(parts) >= 2 and parts[0].isdigit():
                _procs[int(parts[0])] = (parts[1], parts[2] if len(parts) > 2 else "")
    for p in [p for p in _procs if p not in pids]:
        del _procs[p]  # a later process can get the same pid
    return {p: _procs.get(p, ("", "")) for p in pids}


def focus_pane():
    """The iTerm session id of the open focus screen, or None."""
    try:
        with open(TUI_FILE) as f:
            info = json.load(f)
        os.kill(int(info["pid"]), 0)
        return info.get("session") or None
    except (OSError, ValueError, KeyError, TypeError):
        return None


async def take(bridge, sessions, state, with_status=False):
    """The current layout: every iTerm window that shows a Claude session or
    the focus screen, in window order. The bridge's pane map must be fresh.
    with_status (a save for a restart) adds each session's status and tty."""
    live = [s for s in sessions if s.get("kind", "interactive") == "interactive"]
    info = _proc_info([s["pid"] for s in live])
    by_tty = {info[s["pid"]][0]: s for s in live if info[s["pid"]][0] not in ("", "??")}
    focus = focus_pane()
    by_tab = {}
    for sid, tty, tab_id in bridge.known_panes():
        by_tab.setdefault(tab_id, []).append((sid, tty))
    windows = []
    for w in bridge.app.terminal_windows:
        panes = []
        for t in w.tabs:
            for sid, tty in by_tab.get(t.tab_id, []):
                s = by_tty.get(tty)
                if s:
                    p = state.get("pending", {}).get(s["sessionId"])
                    mine = p and p.get("pid") in (None, s["pid"])
                    pane = {"kind": "claude", "sessionId": s["sessionId"], "name": s.get("name"),
                            "cwd": s.get("cwd"), "flags": keep_flags(info[s["pid"]][1]),
                            "pending": {k: p.get(k) for k in PENDING_KEYS} if mine else None}
                    if with_status:
                        pane.update(status=s["status"], tty=tty)
                    panes.append(pane)
                elif sid == focus:
                    panes.append({"kind": "focus", "cwd": os.path.expanduser("~")})
        if not panes:
            continue
        try:
            fr = await w.async_get_frame()
            frame = [fr.origin.x, fr.origin.y, fr.size.width, fr.size.height]
            full = await w.async_get_fullscreen()
        except Exception:  # the window closes while we ask
            continue
        windows.append({"fullscreen": full, "frame": frame, "panes": panes})
    return {"windows": windows}


def record(snap, run, last_sig):
    """Append the snapshot when it differs from the last one. Returns the new
    signature. Each line goes to the disk at once (fsync): a crash loses at
    most a line that was being written, and a reader skips a cut line."""
    sig = json.dumps(snap, sort_keys=True)
    if sig == last_sig:
        return last_sig
    store.ensure()
    line = json.dumps(dict(snap, at=int(time.time() * 1000), run=run), ensure_ascii=False)
    with open(HISTORY, "a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
    return sig


def save(snap, run):
    """Append a snapshot saved for a planned restart (always, also when it is
    the same as the last one). The service opens it by itself after the restart."""
    store.ensure()
    line = dict(snap, at=int(time.time() * 1000), run=run, saved="restart", auto_restore=True)
    with open(HISTORY, "a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    store.log(f"layout saved for a restart: {claude_count(line)} Claude sessions in "
              f"{len(line['windows'])} windows")
    return line


def read_history():
    try:
        f = open(HISTORY)
    except OSError:
        return []
    out = []
    with f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue  # a line cut by the crash
            if isinstance(d, dict) and isinstance(d.get("windows"), list) and "at" in d:
                out.append(d)
    return out


def prune(run):
    """Keep the last KEEP_DAYS days, and always the whole previous run (the
    one a restore needs, however old it is)."""
    try:
        if os.path.getsize(HISTORY) < PRUNE_BYTES:
            return
    except OSError:
        return
    lines = read_history()
    prev = {id(d) for d in last_run_lines(lines, run)}
    cut = (time.time() - KEEP_DAYS * 86400) * 1000
    keep = [d for d in lines if d["at"] >= cut or id(d) in prev]
    tmp = HISTORY + ".tmp"
    with open(tmp, "w") as f:
        for d in keep:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, HISTORY)
    store.log(f"layouts pruned: {len(lines)} -> {len(keep)} snapshots")


# ---- choosing what to open --------------------------------------------------

def last_run_lines(lines, run):
    """The snapshots of the latest run that is not the current one."""
    prev = [d for d in lines if d.get("run") != run]
    if not prev:
        return []
    last = prev[-1].get("run")
    return [d for d in prev if d.get("run") == last]


def claude_count(snap):
    return sum(p["kind"] == "claude" for w in snap["windows"] for p in w["panes"])


def previous_layout(lines, run):
    """The layout to restore: the last snapshot of the previous run. When
    iTerm2 quits, the engine can still see some panes close, so of that
    run's final two minutes take the fullest snapshot (the latest of equals)."""
    mine = last_run_lines(lines, run)
    if not mine:
        return None
    end = mine[-1]["at"]
    # A layout saved for a restart wins, when the run ended soon after it:
    # stopping and closing sessions before the restart changes the layout.
    saved = [d for d in mine if d.get("saved")]
    if saved and end - saved[-1]["at"] <= SAVE_VALID_MS:
        return saved[-1]
    tail = [d for d in mine if end - d["at"] <= QUIT_SPAN_MS]
    return max(reversed(tail), key=claude_count)


def plan(snap, live_ids, focus_running):
    """(windows to open, [(pane, why) skipped]). Never opens a session that
    runs now (two processes on one conversation), a folder that is gone, or
    a second focus screen."""
    windows, skipped, seen = [], [], set()
    for w in snap["windows"]:
        panes = []
        for p in w["panes"]:
            if p["kind"] == "focus":
                if focus_running or "focus" in seen:
                    continue
                seen.add("focus")
            elif p.get("sessionId") in live_ids or p.get("sessionId") in seen:
                continue
            elif not os.path.isdir(p.get("cwd") or ""):
                skipped.append((p, "its folder is gone"))
                continue
            else:
                seen.add(p["sessionId"])
            panes.append(p)
        if panes:
            windows.append(dict(w, panes=panes))
    return windows, skipped


def label(p):
    if p["kind"] == "focus":
        return "claude-focus (the focus screen)"
    return p.get("name") or f"{os.path.basename(p.get('cwd') or '?')} {p['sessionId'][:8]}"


def turn_was_open(cwd, session_id):
    """True when the transcript ends inside a turn: the session was working
    when it stopped, and after a resume it does not go on by itself."""
    path = sessions_mod.transcript_path(cwd, session_id)
    if not path:
        return False
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 300_000))
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return False
    open_turn = False
    for line in raw.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("isSidechain") or e.get("isMeta"):
            continue
        c = (e.get("message") or {}).get("content")
        blocks = [b for b in c if isinstance(b, dict)] if isinstance(c, list) else []
        if e.get("type") == "user":
            text = c if isinstance(c, str) else " ".join(b.get("text", "") for b in blocks)
            if "<command-name>" in text or "<local-command" in text:
                continue
            if "[Request interrupted" in text:
                open_turn = False
            elif text.strip() or any(b.get("type") == "tool_result" for b in blocks):
                open_turn = True
        elif e.get("type") == "assistant":
            if any(b.get("type") == "tool_use" for b in blocks):
                open_turn = True
            elif any(b.get("type") == "text" for b in blocks):
                open_turn = False
    return open_turn


def describe(snap, windows, skipped):
    """The lines that show the user what a restore opens."""
    when = dt.datetime.fromtimestamp(snap["at"] / 1000).strftime("%a %d %b %H:%M")
    n = sum(len(w["panes"]) for w in windows)
    out = [f"Last layout before iTerm2 restarted (saved {when}): {n} to open."]
    for w in windows:
        for p in w["panes"]:
            notes = []
            if p.get("pending"):
                notes.append("its wait is set again")
            if p["kind"] == "claude" and "status" in p:
                notes += [{"busy": "it was working: it continues by itself",
                           "shell": "its background shells ended: it continues by itself",
                           "waiting": "it asked you something: it asks again"}.get(p["status"], "")]
            elif p["kind"] == "claude" and turn_was_open(p.get("cwd"), p["sessionId"]):
                notes.append("it was working: tell it to continue")
            notes = [n for n in notes if n]
            where = "" if p["kind"] == "focus" else f"  {p.get('cwd')}"
            out.append(f"  open  {label(p)}{where}" + (f"  ({'; '.join(notes)})" if notes else ""))
    for p, why in skipped:
        out.append(f"  skip  {label(p)}: {why}")
    return out


# ---- opening (iTerm2) --------------------------------------------------------

def shell_line(p):
    if p["kind"] == "focus":
        return "claude-focus"
    cmd = " ".join(["claude", *map(shlex.quote, p.get("flags") or []), "--resume", p["sessionId"]])
    message = RESUME_MESSAGES.get(p.get("status"))
    if message:  # `claude -r <id> "<prompt>"` sends it as the first message
        cmd += " " + shlex.quote(store.say(message))
    return f"cd {shlex.quote(p['cwd'])} && {cmd}"


async def _wait_for_shell(session, seconds=10):
    """Type only after the login shell runs: before that, `login` owns the tty."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            job = await session.async_get_variable("jobName")
        except Exception:
            job = None
        if job and job != "login":
            break
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)


async def run(conn, windows, say):
    """Open the planned windows one by one, then set the waits again.
    say(text) reports progress; every step also goes to the log."""
    import iterm2

    def note(text):
        store.log("restore: " + text)
        say(text)

    # Without an App on this connection, a new window comes back without its tabs.
    await iterm2.async_get_app(conn)
    waits = []
    for w in windows:
        win = await iterm2.Window.async_create(conn)
        if win is None or not (win.current_tab or win.tabs):
            note("iTerm2 did not open a window")
            continue
        try:
            await _place(iterm2, win, w)
        except Exception as ex:  # a window in the wrong place is still useful
            store.log(f"restore: window not placed: {ex!r}")
        for n, p in enumerate(w["panes"]):
            tab = (win.current_tab or win.tabs[0]) if n == 0 else await win.async_create_tab()
            session = tab.current_session or tab.sessions[0]
            await _wait_for_shell(session)
            await session.async_send_text(shell_line(p) + "\r")
            note(f"opened {label(p)}")
            if p.get("pending"):
                waits.append(p)
        if w.get("fullscreen") and not await win.async_get_fullscreen():
            await win.async_set_fullscreen(True)
        await asyncio.sleep(OPEN_PAUSE)
    if waits:
        await _rearm(waits, note)
    note("done")


async def _place(iterm2, win, w):
    """Move a new window to its old frame (and so its old display). A new
    window can open in full screen already (iTerm2 does that when the key
    window is full screen): it must leave full screen to move, or when it
    was a normal window. The caller enters full screen after typing."""
    frame = w.get("frame")
    if await win.async_get_fullscreen():
        fr = await win.async_get_frame()
        same_display = frame and abs(fr.origin.x - frame[0]) < 2 and abs(fr.origin.y - frame[1]) < 2
        if w.get("fullscreen") and (same_display or not frame):
            return
        await win.async_set_fullscreen(False)
        await asyncio.sleep(1.0)  # the full-screen animation
    if frame:
        x, y, width, height = (int(v) for v in frame)
        await win.async_set_frame(iterm2.Frame(iterm2.Point(x, y), iterm2.Size(width, height)))


async def _rearm(waits, note):
    """Set each wait again for the resumed process (its pid is new). A wait
    whose time passed while the computer was down wakes the session at once."""
    end = time.time() + REARM_SECONDS
    todo = {p["sessionId"]: p for p in waits}
    while todo and time.time() < end:
        rows = await asyncio.get_event_loop().run_in_executor(None, sessions_mod.list_sessions) or []
        for row in rows:
            p = todo.pop(row["sessionId"], None)
            if p:
                w = p["pending"]
                pend.set_pending(row, w.get("until"), w.get("check"), w.get("every_min") or 5, w.get("note") or "",
                                 "" if w.get("needs_done") else w.get("needs") or "")
                note(f"wait set again for {label(p)}")
        if todo:
            await asyncio.sleep(2)
    for p in todo.values():
        note(f"wait NOT set again for {label(p)}: the session did not start in {REARM_SECONDS} s")


# ---- a planned restart --------------------------------------------------------

def describe_save(snap, sessions):
    """The lines that show the user what `claude-focus reboot` saves."""
    panes = [p for w in snap["windows"] for p in w["panes"]]
    claude = [p for p in panes if p["kind"] == "claude"]
    full = sum(1 for w in snap["windows"] if w.get("fullscreen"))
    out = [f"Layout to save: {len(snap['windows'])} windows ({full} full screen), "
           f"{len(claude)} Claude sessions."]
    after = {"busy": "WORKING: is stopped now, continues by itself after the restart",
             "shell": "background shells: they end, the session continues after the restart",
             "waiting": "asks you something: asks again after the restart"}
    for p in panes:
        notes = [after.get(p.get("status"), "")]
        if p.get("pending"):
            notes.append("its wait is set again")
        notes = [n for n in notes if n]
        out.append(f"  {label(p):<34}" + ("; ".join(notes) if notes else ""))
    seen = {p.get("sessionId") for p in claude}
    other = [s.get("name") or s["sessionId"][:8] for s in sessions if s["sessionId"] not in seen]
    if other:
        out.append("Not in an iTerm2 pane, so not restored: " + ", ".join(other))
    return out


async def stop_working(bridge, panes, say):
    """Interrupt each working session (Esc, as the user would) and wait until it
    stops, so the restart cuts no half-written answer."""
    by_tty = {tty: sid for sid, tty, _ in bridge.known_panes()}
    for p in panes:
        session = bridge.session(by_tty.get(p.get("tty")))
        if session is None:
            say(f"  {label(p)}: no iTerm2 pane found, not stopped")
            continue
        await session.async_send_text("\x1b")
    end = time.time() + STOP_SECONDS
    todo = {p["sessionId"]: p for p in panes}
    while todo and time.time() < end:
        await asyncio.sleep(1)
        rows = await asyncio.get_event_loop().run_in_executor(None, sessions_mod.list_sessions) or []
        for row in rows:
            if row["sessionId"] in todo and row["status"] != "busy":
                say(f"  {label(todo.pop(row['sessionId']))}: stopped")
    for p in todo.values():
        say(f"  {label(p)}: still working after {STOP_SECONDS} s; stop it with Esc before the restart")


async def auto(conn, run_id, say=lambda text: None):
    """In the engine at start: open the layout saved for a restart, once,
    without a key press. Returns the number of panes it opens."""
    snap = previous_layout(read_history(), run_id)
    if not snap or not snap.get("auto_restore"):
        return 0
    if store.load_state().get("restore_handled") == snap["at"]:
        return 0
    store.update_state(lambda st: st.__setitem__("restore_handled", snap["at"]))  # never twice
    rows = await asyncio.get_event_loop().run_in_executor(None, sessions_mod.list_sessions) or []
    windows, skipped = plan(snap, {r["sessionId"] for r in rows}, bool(focus_pane()))
    n = sum(len(w["panes"]) for w in windows)
    when = dt.datetime.fromtimestamp(snap["at"] / 1000).strftime("%a %d %b %H:%M")
    store.log(f"restore: opening the layout saved for a restart ({when}) by itself: {n} panes")
    for p, why in skipped:
        store.log(f"restore: skip {label(p)}: {why}")
    if windows:
        await run(conn, windows, say)
    return n
