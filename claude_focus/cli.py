"""Entry point. `claude-focus` opens the focus screen; `claude-focus list`
prints the queue once (it scores sessions that have no fresh score first)."""
import os
import subprocess
import sys
import time

from . import store, terminal


def print_queue(app):
    from .app import fmt_wait, fmt_when
    v = app.view
    print(f"NEED YOU ({len(v['need'])})")
    for i in v["need"]:
        sc = i["fresh"] or {}
        dup = " [DUPLICATE window]" if i["s"].get("dup") else ""
        print(f"  {i['eff']:>5.1f}  base {i['base']:>3}  {i['s'].get('name', '?'):<32} "
              f"{fmt_wait(i['waited_h']):>7}  {sc.get('kind', '?'):<8} {sc.get('ask', '(no score)')}{dup}")
        if sc.get("reason"):
            print(f"{'':>16}why: {sc['reason']}" + (f"  deadline {sc['deadline']}" if sc.get("deadline") else ""))
    print(f"YOUR TO-DOS ({len(v['todo'])})")
    for i in v["todo"]:
        print(f"  {fmt_wait(i['waited_h']):>7}  {i['s'].get('name', '?'):<32} {i['needs']}")
    print(f"PENDING ({len(v['pending'])})")
    for i in v["pending"]:
        print(f"  {fmt_when(i['wake']):>9}  {i['s'].get('name', '?'):<32} {i.get('why', '')}")
    print(f"FREE ({len(v['free'])})")
    for i in v["free"]:
        print(f"  {i['s'].get('name', '?'):<32} {i.get('why', '')}")
    print(f"WORKING ({len(v['working'])}): " + ", ".join(i["s"].get("name", "?") for i in v["working"]))


def cmd_list():
    import asyncio

    from .app import App

    async def run(bridge):
        app = App(bridge)
        store.ensure()
        await app.bridge.start()
        await app.poll()
        for _ in range(150):  # wait at most 150 s for scores
            if not app.scoring:
                break
            await asyncio.sleep(1)
        await app.poll()
        print_queue(app)

    terminal.run(run)


def cmd_go():
    """Go to the session at the top of the queue (a herdr key runs this; in
    iTerm2, the hotkey "hotkey_go" of a focus window does the same)."""
    from .app import App

    async def run(bridge):
        app = App(bridge, passive=True)
        app.cfg["return_after_answer"] = False  # this process ends at once; the focus window watches
        store.ensure()
        await bridge.start()
        await app.poll()
        if app.selected() == (None, None):
            app.flash("Nothing needs you now.")
        else:
            await app.go_top()
        if app.msg:
            print(app.msg)
            await bridge.notify(app.msg)

    terminal.run(run)


def cmd_show():
    """Bring the focus screen to the front, or open one."""
    from .app import show_focus

    async def run(bridge):
        await bridge.start()
        await bridge.refresh()
        await show_focus(bridge)

    terminal.run(run)


def _herdr_keeps_layout(command):
    """True (and says so) when the sessions run in herdr: its server keeps
    every pane and resumes the Claude sessions in them after a restart."""
    if terminal.detect() != terminal.HERDR:
        return False
    print(f"claude-focus {command}: your sessions run in herdr, which keeps its layout and resumes "
          "the Claude sessions in it after a restart. Nothing to do here.")
    return True


PENDING_HELP = """claude-focus pending: wait for something external without {user}.

Run it from inside a Claude Code session (its Bash tool). The session leaves
{user}'s queue. When the time comes, or when the check command exits 0, the
focus app types a wake-up message into the session so it continues by itself.

  claude-focus pending --until 09:00 --note "launch check"
  claude-focus pending --check 'git ls-remote origin refs/tags/v2.0 | grep -q .' --every 10 \\
                       --until "2026-10-02 18:00" --note "the v2.0 release tag"
  claude-focus pending --show | --clear

If {user} must also do something meanwhile (review and send a draft, sign,
pay), add --needs "<what they must do>". The session then shows under YOUR
TO-DOS with that request until they mark it done (d), and the wait still
wakes it. Without --needs, a wait set before {user} saw the answer to their
last message (in the turn that answers it, or in a later turn that a
background task started) shows under YOUR TO-DOS as "Read its answer to
your message" until they look at the session.

WHEN: HH:MM (next time it comes), +30m, +2h, +1d, or "YYYY-MM-DD HH:MM".
--until with --check is the latest time: the session wakes then even if the
check never succeeds. The wait ends early when {user} types into the session;
a background task notification, a message from another session, the answer
to a question and an idle compaction keep it."""


def cmd_pending(argv):
    import argparse
    import datetime as dt

    from . import pending as pend
    from . import queue as q
    from . import sessions as sessions_mod

    ap = argparse.ArgumentParser(prog="claude-focus pending", description=store.say(PENDING_HELP),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--until", help="wake at this time (with --check: the latest time)")
    ap.add_argument("--check", help="shell command; exit 0 means the event happened")
    ap.add_argument("--every", type=int, default=5, help="minutes between checks (default 5)")
    ap.add_argument("--note", default="", help="what the session waits for")
    ap.add_argument("--needs", default="", help=store.say("what {user} must do meanwhile (the session "
                                                          "stays in their queue)"))
    ap.add_argument("--clear", action="store_true", help="end this session's wait")
    ap.add_argument("--show", action="store_true", help="show this session's wait")
    a = ap.parse_args(argv)

    sess = pend.own_session()
    if not sess:
        sys.exit("claude-focus pending: run this from inside a Claude Code session.")
    sid = sess["sessionId"]
    if a.clear:
        pend.clear_pending(sid)
        print(f"Wait cleared for {sess.get('name')}.")
        return
    if a.show:
        p = store.load_state().get("pending", {}).get(sid)
        print(p if p else "No wait set.")
        return
    if not a.until and not a.check:
        ap.error("give --until, --check, or both")
    until_ms = None
    if a.until:
        when = q.parse_when(a.until, dt.datetime.now())
        if not when or when <= dt.datetime.now():
            ap.error(f"--until: cannot read a future time from {a.until!r}")
        until_ms = int(when.timestamp() * 1000)
    every = max(1, a.every)
    needs, why = a.needs, None
    if not needs:  # the answer to the user's message must reach them
        why = pend.read_answer_reason(store.load_state(), sess,
                                      sessions_mod.last_user_answer(sess.get("cwd"), sid))
        needs = pend.READ_ANSWER if why else ""
    auto = bool(why)
    pend.set_pending(sess, until_ms, a.check, every, a.note, needs, needs_auto=auto)
    parts = []
    if until_ms:
        parts.append(("at the latest at " if a.check else "at ") + pend.fmt_ms(until_ms))
    if a.check:
        parts.insert(0, f"when the check succeeds (checked every {every} min)")
    user = store.user()
    if auto:
        print(f"Pending: {why}, so {sess.get('name')} shows under "
              f"YOUR TO-DOS in their queue ({needs!r}) until they look at the session; then it leaves "
              "their queue. claude-focus wakes it " + " or ".join(parts) + ", with a message in its prompt.")
    elif a.needs:
        print(f"Pending: {sess.get('name')} shows under YOUR TO-DOS in {user}'s queue with your "
              f"request ({a.needs!r}) until they mark it done. claude-focus wakes it "
              + " or ".join(parts) + ", with a message in its prompt.")
    else:
        print(f"Pending: {sess.get('name')} leaves {user}'s queue. claude-focus wakes it "
              + " or ".join(parts) + ", with a message in its prompt.")
    if not pend.app_running():
        print("WARNING: neither the claude-focus service nor a focus screen runs now, so no "
              f"one wakes this session. Tell {user}.")


def cmd_restore(argv):
    """Open the last layout of the previous iTerm2 run again (after a crash)."""
    import argparse

    import iterm2

    from . import restore
    from . import sessions as sessions_mod

    ap = argparse.ArgumentParser(prog="claude-focus restore", description=restore.__doc__)
    ap.add_argument("--dry-run", action="store_true", help="only show what it would open")
    ap.add_argument("--yes", action="store_true", help="do not ask before opening")
    a = ap.parse_args(argv)
    if _herdr_keeps_layout("restore"):
        return
    snap = restore.previous_layout(restore.read_history(), restore.iterm_run())
    if not snap:
        print("No saved layout of an earlier iTerm2 run.")
        return
    rows = sessions_mod.list_sessions()
    if rows is None:
        sys.exit("claude-focus restore: cannot read the running Claude sessions.")
    windows, skipped = restore.plan(snap, {r["sessionId"] for r in rows}, bool(restore.focus_pane()))
    print("\n".join(restore.describe(snap, windows, skipped)))
    if not windows or a.dry_run:
        return
    if not a.yes and input("Open them now? [y/N] ").strip().lower() != "y":
        return
    store.update_state(lambda st: st.__setitem__("restore_handled", snap["at"]))
    iterm2.run_until_complete(lambda conn: restore.run(conn, windows, print))


AFTER_REBOOT = """
Saved. Now:
  1. Restart the Mac (Apple menu > Restart). In the dialog, untick "Reopen windows
     when logging back in". If iTerm2 asks about running sessions, close them.
  2. After you log in, open iTerm2. The claude-focus service then opens every
     window by itself (after about 10 s, then one window every 2 s), on the same
     displays and full screen as before. Do not type in iTerm2 while it works.
  3. Working sessions continue by themselves, sessions that asked you something
     ask again, and waits are set again.
If no window opens within 2 minutes, run: claude-focus restore
If you restart more than 30 minutes from now, run claude-focus reboot again."""


def cmd_reboot(argv):
    """Before a planned restart: save the layout with each session's status,
    stop the working sessions, and print what to do. After the restart, the
    service opens the layout by itself."""
    import argparse

    import iterm2

    from . import restore
    from . import sessions as sessions_mod
    from .iterm import Bridge

    ap = argparse.ArgumentParser(prog="claude-focus reboot", description=cmd_reboot.__doc__)
    ap.add_argument("--dry-run", action="store_true", help="only show what it would save")
    ap.add_argument("--yes", action="store_true", help="do not ask")
    a = ap.parse_args(argv)
    if _herdr_keeps_layout("reboot"):
        return

    async def run(conn):
        bridge = Bridge(conn)
        await bridge.start()
        await bridge.pane_map()
        rows = sessions_mod.list_sessions()
        if rows is None:
            sys.exit("claude-focus reboot: cannot read the running Claude sessions.")
        snap = await restore.take(bridge, rows, store.load_state(), with_status=True)
        print("\n".join(restore.describe_save(snap, rows)))
        if a.dry_run or not snap["windows"]:
            return
        working = [p for w in snap["windows"] for p in w["panes"] if p.get("status") == "busy"]
        stop = f" and stop the {len(working)} working session(s)" if working else ""
        if not a.yes and input(f"Save this layout{stop}? [y/N] ").strip().lower() != "y":
            print("Nothing saved.")
            return
        restore.save(snap, restore.iterm_run())
        if working:
            print("Stopping the working sessions:")
            await restore.stop_working(bridge, working, print)
        print(AFTER_REBOOT)

    iterm2.run_until_complete(run)


def cmd_hook(argv):
    """Claude Code hooks (~/.claude/settings.json). `hook prompt-submit` is
    the UserPromptSubmit hook: it ends the wait of a session that the user types
    into and tells Claude so. It never blocks a prompt."""
    import json

    from . import pending as pend

    try:
        if argv[:1] == ["prompt-submit"]:
            context = pend.on_prompt(json.load(sys.stdin))
            if context:
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit", "additionalContext": context}}))
    except Exception as ex:  # a broken hook must not stop the user's prompt
        store.log(f"hook error {argv}: {ex!r}")


LABEL = "com.claude-focus.service"
PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")


iterm_running = terminal.iterm_running


def cmd_daemon():
    """The background service (launchd runs this). It waits for a terminal
    (terminal.detect: herdr or iTerm2), then runs the engine: scoring, waker,
    hotkeys. When that terminal quits, or the sessions move to the other one,
    it exits and launchd starts it again."""
    from .app import App

    store.ensure()
    while not (kind := terminal.detect()):  # never let AppleScript start iTerm2
        time.sleep(20)
    store.log(f"service start in {kind} (pid {os.getpid()})")
    try:
        terminal.run(lambda bridge: App(bridge, headless=True).run_headless(), kind)
    finally:
        store.log(f"service stop (pid {os.getpid()})")


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def lasting(path):
    """A path in Homebrew's Cellar (<prefix>/Cellar/claude-focus/0.1.0/...)
    as the same path under brew's opt link (<prefix>/opt/claude-focus/...),
    which brew keeps across upgrades; the Cellar folder goes away. Any other
    path as it is."""
    head, sep, tail = path.partition("/Cellar/")
    name, _, rest = tail.partition("/")
    rest = rest.partition("/")[2]
    if sep and rest and os.path.isdir(f"{head}/opt/{name}"):
        return f"{head}/opt/{name}/{rest}"
    return path


def cmd_install_daemon():
    import plistlib

    store.ensure()
    # The Python that runs this command (a venv's, or the one pip installed
    # into), with the folder that holds the package on PYTHONPATH, so the
    # service also runs from a checkout that is not installed.
    root = lasting(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    plist = {
        "Label": LABEL,
        "ProgramArguments": [lasting(sys.executable), "-m", "claude_focus", "daemon"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "ProcessType": "Interactive",
        "StandardOutPath": os.path.join(store.DIR, "service.log"),
        "StandardErrorPath": os.path.join(store.DIR, "service.log"),
        # The PATH of the shell that installs the service, so the scorer
        # finds `claude` and check commands find the tools they call.
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": root},
    }
    os.makedirs(os.path.dirname(PLIST), exist_ok=True)
    with open(PLIST, "wb") as f:
        plistlib.dump(plist, f)
    domain = f"gui/{os.getuid()}"
    _stop_service(domain)
    # A bootstrap right after a bootout can fail with "5: Input/output
    # error" (seen 2026-10-21, while the old service was still going away).
    for attempt in range(5):
        r = _launchctl("bootstrap", domain, PLIST)
        if r.returncode == 0:
            break
        time.sleep(1 + attempt)
    else:
        sys.exit(f"launchctl bootstrap failed: {r.stderr.strip()}")
    print(f"Installed and started {LABEL}. Log: {plist['StandardOutPath']}")


def _stop_service(domain, seconds=10):
    """Boot the service out and wait until launchd no longer knows it:
    bootout returns before the old process is gone."""
    _launchctl("bootout", f"{domain}/{LABEL}")
    deadline = time.time() + seconds
    while time.time() < deadline and _launchctl("print", f"{domain}/{LABEL}").returncode == 0:
        time.sleep(0.3)


def cmd_uninstall_daemon():
    _stop_service(f"gui/{os.getuid()}")
    if os.path.exists(PLIST):
        os.remove(PLIST)
    print(f"Stopped and removed {LABEL}.")


def cmd_service_status():
    print(f"terminal: {terminal.describe(terminal.detect())}")
    r = _launchctl("print", f"gui/{os.getuid()}/{LABEL}")
    if r.returncode != 0:
        print("Service not installed.")
        return
    for line in r.stdout.splitlines():
        if line.strip().startswith(("state =", "pid =", "last exit code")):
            print(line.strip())


def main():
    args = sys.argv[1:]
    if not args:
        from .app import main as tui
        tui()
    elif args[0] in ("--version", "-V"):
        from . import __version__
        print(f"claude-focus {__version__}")
    elif args[0] == "list":
        cmd_list()
    elif args[0] == "go":
        cmd_go()
    elif args[0] == "show":
        cmd_show()
    elif args[0] == "pending":
        cmd_pending(args[1:])
    elif args[0] == "hook":
        cmd_hook(args[1:])
    elif args[0] == "restore":
        cmd_restore(args[1:])
    elif args[0] == "reboot":
        cmd_reboot(args[1:])
    elif args[0] == "daemon":
        cmd_daemon()
    elif args[0] == "install-daemon":
        cmd_install_daemon()
    elif args[0] == "uninstall-daemon":
        cmd_uninstall_daemon()
    elif args[0] == "status":
        cmd_service_status()
    elif args[0] == "cost":
        from . import costs
        print(costs.report())
    elif args[0] == "setup" and set(args[1:]) <= {"--yes", "--dry-run"}:
        from . import setup
        setup.Setup(yes="--yes" in args, dry_run="--dry-run" in args).run()
    elif args[0] == "uninstall" and set(args[1:]) <= {"--purge"}:
        from . import setup
        setup.uninstall(purge="--purge" in args)
    else:
        print("usage: claude-focus [setup [--yes] [--dry-run] | uninstall [--purge] | list | go | show | "
              "pending --help | restore [--dry-run] | reboot [--dry-run] | cost | status | install-daemon | "
              "uninstall-daemon | --version]")
        sys.exit(2)
