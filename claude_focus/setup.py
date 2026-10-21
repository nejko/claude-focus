"""`claude-focus setup` and `claude-focus uninstall`: every step after the
install, in one command. Each step checks first and skips what is done, so
setup can run again at any time (after an upgrade it restarts the service on
the new code).

setup:
 1. checks: macOS, the terminal (herdr or iTerm2, see terminal.detect),
    `claude` on PATH, where this command lives
 2. the terminal's API: herdr's socket, or the iTerm2 Python API (macOS asks
    once whether this program may control iTerm2)
 3. ~/.claude-focus/config.json: your name
 4. the UserPromptSubmit hook in ~/.claude/settings.json
 5. the rule for waits in ~/.claude/CLAUDE.md
 6. free terminals: in iTerm2 a green profile, switched automatically
    (optional); in herdr every pane at a bare shell. herdr only: its Claude
    Code integration, and two herdr keys (go to the top, show the focus screen)
 7. the background service: installed, or restarted on the new code
 8. the focus screen in a new iTerm2 window or herdr tab

uninstall undoes 4 to 7, and with --purge also removes ~/.claude-focus.
Files it changes get a backup next to them the first time."""
import contextlib
import io
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import time

from . import herdr, store, terminal

CLAUDE_DIR = os.path.expanduser("~/.claude")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
CLAUDE_MD = os.path.join(CLAUDE_DIR, "CLAUDE.md")
PROFILES_DIR = os.path.expanduser("~/Library/Application Support/iTerm2/DynamicProfiles")
PROFILE_FILE = os.path.join(PROFILES_DIR, "claude-focus.json")
PROFILE_GUID = "719CE515-5BA5-45D6-880C-834DB5E01564"  # fixed: setup and uninstall find it again
HERDR_CONFIG = os.path.expanduser(os.environ.get("HERDR_CONFIG_PATH") or "~/.config/herdr/config.toml")
RULE_FILE = os.path.join(os.path.dirname(__file__), "claude_md_rule.md")
HOOK_TAG = "claude-focus hook prompt-submit"
RULE_TAG = "claude-focus pending"  # any CLAUDE.md that mentions it has a rule already
MARK_START = "<!-- claude-focus setup: start (claude-focus uninstall removes this block) -->"
MARK_END = "<!-- claude-focus setup: end -->"
KEYS_START = "# claude-focus setup: start (claude-focus uninstall removes this block)"
KEYS_END = "# claude-focus setup: end"
BACKUP = ".before-claude-focus"
GREEN_DARK, GREEN_LIGHT = (24, 97, 29), (205, 240, 205)
SERVICE_WAIT = 30  # seconds to wait for the service to reach the terminal


# ---- pure parts (tested) ----------------------------------------------------

def add_hook(settings, command):
    """Add the UserPromptSubmit hook to a settings dict. Returns True when it
    changed it (False: a claude-focus hook is there already)."""
    hooks = settings.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
    if any(HOOK_TAG in h.get("command", "") for group in hooks for h in group.get("hooks", [])):
        return False
    hooks.append({"hooks": [{"type": "command", "command": command, "timeout": 10}]})
    return True


def remove_hook(settings):
    """Remove every claude-focus hook. Returns True when it removed one."""
    hooks = settings.get("hooks", {}).get("UserPromptSubmit")
    if not hooks:
        return False
    kept, removed = [], False
    for group in hooks:
        inner = [h for h in group.get("hooks", []) if HOOK_TAG not in h.get("command", "")]
        removed |= len(inner) != len(group.get("hooks", []))
        if inner:
            kept.append(dict(group, hooks=inner))
    if kept:
        settings["hooks"]["UserPromptSubmit"] = kept
    else:
        del settings["hooks"]["UserPromptSubmit"]
        if not settings["hooks"]:
            del settings["hooks"]
    return removed


def add_rule(text, rule):
    """CLAUDE.md text with the rule added at the end, between markers.
    Returns (text, changed)."""
    if RULE_TAG in text:
        return text, False
    head = text.rstrip() + "\n\n" if text.strip() else ""
    return f"{head}{MARK_START}\n\n{rule.strip()}\n\n{MARK_END}\n", True


def remove_rule(text, mark_start=MARK_START, mark_end=MARK_END):
    """CLAUDE.md text without the block that setup added. Returns (text, changed)."""
    start, end = text.find(mark_start), text.find(mark_end)
    if start < 0 or end < start:
        return text, False
    before, after = text[:start].rstrip(), text[end + len(mark_end):].lstrip("\n")
    return (before + "\n\n" + after if before and after else before + "\n" if before else after), True


def herdr_keys(command, go, show):
    """The block of herdr key bindings (herdr's config.toml) that setup adds:
    custom commands of type "shell" run detached."""
    def binding(key, args):
        line = json.dumps(f"{shlex.quote(command)} {args}")  # a JSON string is a TOML basic string
        return f'[[keys.command]]\nkey = {json.dumps(key)}\ntype = "shell"\ncommand = {line}\n'
    return f"{KEYS_START}\n{binding(go, 'go')}\n{binding(show, 'show')}{KEYS_END}\n"


def add_herdr_keys(text, block):
    """herdr's config.toml with the block at the end. Returns (text, changed)."""
    if KEYS_START in text:
        return text, False
    head = text.rstrip() + "\n\n" if text.strip() else ""
    return head + block, True


def remove_herdr_keys(text):
    return remove_rule(text, KEYS_START, KEYS_END)


def _color(rgb):
    r, g, b = (c / 255 for c in rgb)
    return {"Red Component": r, "Green Component": g, "Blue Component": b,
            "Alpha Component": 1, "Color Space": "sRGB"}


def profile_plist(name):
    """An iTerm2 dynamic profile: the default profile with a green background.
    Without a parent, iTerm2 takes every other setting from the default profile."""
    return {"Profiles": [{
        "Name": name, "Guid": PROFILE_GUID,
        "Background Color": _color(GREEN_DARK),
        "Background Color (Dark)": _color(GREEN_DARK),
        "Background Color (Light)": _color(GREEN_LIGHT),
    }]}


def first_name():
    """The first word of this Mac user's full name, else the account name."""
    try:
        full = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
    except KeyError:
        full = ""
    return (full.split() or [os.environ.get("USER", "")])[0]


def command_path():
    """How the hook and the focus window run claude-focus: the command on
    PATH (stays valid across upgrades), else this program's own path."""
    return shutil.which("claude-focus") or os.path.abspath(sys.argv[0])


# ---- running it --------------------------------------------------------------

class Setup:
    def __init__(self, yes=False, dry_run=False):
        self.yes, self.dry_run = yes, dry_run
        self.todo = []  # what the user must still do, printed at the end

    def say(self, mark, text):
        print(f"  {mark} {text}")

    def done(self, text):
        self.say("✓", text)

    def change(self, text):
        self.say("·" if self.dry_run else "+", ("would " if self.dry_run else "") + text)

    def warn(self, text):
        self.say("!", text)

    def ask(self, question, default):
        if self.yes or self.dry_run or not sys.stdin.isatty():
            return default
        answer = input(f"  ? {question} [{default}] ").strip()
        return answer or default

    def confirm(self, question, default=True):
        if self.yes or self.dry_run or not sys.stdin.isatty():
            return default
        answer = input(f"  ? {question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        return default if not answer else answer.startswith("y")

    def backup(self, path):
        if os.path.exists(path) and not os.path.exists(path + BACKUP):
            shutil.copy2(path, path + BACKUP)

    def write(self, path, text):
        if self.dry_run:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.backup(path)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, path)

    # 1
    def checks(self):
        """Returns the terminal: terminal.HERDR or terminal.ITERM."""
        if sys.platform != "darwin":
            sys.exit("claude-focus needs macOS, and herdr or iTerm2.")
        kind = terminal.detect()
        if kind is None:
            if os.path.exists("/Applications/iTerm.app"):
                self.change("start iTerm2")
                if not self.dry_run:
                    subprocess.run(["open", "-a", "iTerm"])
                    time.sleep(3)
                kind = terminal.ITERM
            elif shutil.which("herdr"):
                sys.exit("herdr does not run. Start herdr, then run `claude-focus setup` in one of its panes.")
            else:
                sys.exit("claude-focus needs herdr (https://herdr.dev) or iTerm2 (https://iterm2.com). "
                         "Install one, then run setup again.")
        self.done(f"terminal: {terminal.describe(kind)}")
        self.done(f"command: {command_path()}")
        if not shutil.which("claude-focus"):
            self.warn("claude-focus is not on your PATH. With uv, run `uv tool update-shell` and open "
                      "a new terminal; the hook and the service use the full path above meanwhile.")
            self.todo.append("Put claude-focus on your PATH (`uv tool update-shell`).")
        cfg = store.load_config()
        if cfg["scorer"] == "claude" and not shutil.which("claude"):
            self.warn("`claude` is not on PATH: the scorer runs `claude -p`. Install Claude Code, or run "
                      "setup from a shell where `claude` works.")
            self.todo.append("Make `claude` available on PATH, then run `claude-focus setup` again.")
        return kind

    # 2 (herdr)
    def herdr_api(self, path):
        pong = herdr.ping(path)
        if not pong:
            self.warn(f"herdr does not answer at {path}. Start herdr (or set herdr_socket in {store.CONFIG}), "
                      "then run `claude-focus setup` again.")
            self.todo.append("Start herdr, then run `claude-focus setup` again in one of its panes.")
            return False
        self.done(f"herdr {pong.get('version')} API: connected ({path})")
        if pong.get("protocol") != herdr.PROTOCOL:
            self.warn(f"herdr speaks API protocol {pong.get('protocol')}; claude-focus was written for "
                      f"{herdr.PROTOCOL} (herdr 0.9.5), so some of it may not work")
        return True

    # 2
    def iterm_api(self):
        """Connect to iTerm2 once. The first time, macOS asks whether this
        program may control iTerm2 (iTerm2 hands out API access through
        AppleScript)."""
        for attempt in range(3):
            ok, why = _probe_iterm()
            if ok:
                self.done("iTerm2 Python API: connected")
                return True
            if self.dry_run or self.yes or not sys.stdin.isatty():
                break
            print(f"\n  The iTerm2 Python API does not answer ({why}).\n"
                  "  In iTerm2: Settings > General > Magic > tick \"Enable Python API\".\n"
                  "  If macOS asks whether claude-focus (or Python) may control iTerm2, allow it.\n")
            input("  Press Enter when done... ")
        self.warn("the iTerm2 Python API does not answer. Turn it on in iTerm2 (Settings > General > "
                  "Magic > Enable Python API), then run `claude-focus setup` again.")
        self.todo.append("Turn on iTerm2's Python API, then run `claude-focus setup` again.")
        return False

    # 3
    def config(self):
        cfg = store._read(store.CONFIG, {})
        if cfg.get("user_name"):
            self.done(f"config: sessions call you {cfg['user_name']}")
            return
        name = self.ask("How should sessions call you?", first_name() or "the user")
        self.change(f"write {store.CONFIG} (user_name {name!r})")
        if not self.dry_run:
            store.ensure()
            store._write(store.CONFIG, dict(cfg, user_name=name))

    # 4
    def hook(self):
        try:
            with open(SETTINGS) as f:
                settings = json.load(f)
        except FileNotFoundError:
            settings = {}
        except ValueError as ex:
            self.warn(f"{SETTINGS} is not valid JSON ({ex}); the hook is not added. Fix the file, then "
                      "run setup again.")
            self.todo.append(f"Fix {SETTINGS}, then run `claude-focus setup` again.")
            return
        command = f"{command_path()} hook prompt-submit"
        if not add_hook(settings, command):
            self.done("prompt hook: already in ~/.claude/settings.json")
            return
        self.change(f"add the prompt hook to {SETTINGS}")
        self.write(SETTINGS, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")

    # 5
    def rule(self):
        text = open(CLAUDE_MD).read() if os.path.exists(CLAUDE_MD) else ""
        new, changed = add_rule(text, open(RULE_FILE).read())
        if not changed:
            self.done("CLAUDE.md: the rule for waits is there")
            return
        self.change(f"add the rule for waits to {CLAUDE_MD} (sessions started before it learn it on restart)")
        self.write(CLAUDE_MD, new)

    # 6
    def free_terminals(self, info):
        cfg = store._read(store.CONFIG, {})
        name = cfg.get("free_profile", store.DEFAULT_CONFIG["free_profile"])
        if cfg.get("auto_profiles"):
            self.done("free terminals: on")
            return
        if not self.confirm("Show free terminals? When a session ends, its pane turns green and shows "
                            "under AVAILABLE TERMINALS, ready for the next session", True):
            self.done("free terminals: off (run setup again to turn them on)")
            return
        if name in info.get("profiles", []):
            self.done(f"iTerm2 profile {name!r}: exists")
        else:
            self.change(f"add the green iTerm2 profile {name!r} ({PROFILE_FILE})")
            self.write(PROFILE_FILE, json.dumps(profile_plist(name), indent=2) + "\n")
            if not self.dry_run and not _wait_for_profile(name):
                self.warn(f"iTerm2 did not load the profile {name!r} (a dynamic profile); free "
                          "terminals stay empty until it exists")
        update = {"auto_profiles": True}
        default = info.get("default_profile")
        if default and default != store.DEFAULT_CONFIG["session_profile"] and "session_profile" not in cfg:
            update["session_profile"] = default  # a session's pane goes back to the user's default profile
        self.change("turn on automatic profile switching in config.json")
        if not self.dry_run:
            store.ensure()
            store._write(store.CONFIG, dict(store._read(store.CONFIG, {}), **update))

    # 6 (herdr)
    def herdr_integration(self, path):
        """herdr's Claude Code hook tells herdr the session id in each pane:
        claude-focus then finds a session's pane even when its process is not
        in the foreground (without it, by its process only)."""
        found = herdr.call(path, "integration.list") or {}
        state = next((i.get("state") for i in found.get("integrations", []) if i.get("target") == "claude"), None)
        if state == "current":
            self.done("herdr's Claude Code integration: installed")
            return
        if not self.confirm("Install herdr's Claude Code integration (a SessionStart hook in "
                            "~/.claude/settings.json that tells herdr the session in each pane)?", True):
            self.done("herdr's Claude Code integration: not installed (sessions are found by their process)")
            return
        self.change("install herdr's Claude Code integration (herdr integration install claude)")
        if not self.dry_run and herdr.call(path, "integration.install", {"target": "claude"}) is None:
            self.warn("herdr did not install its Claude Code integration")
            self.todo.append("Run `herdr integration install claude`.")

    def herdr_keys(self, path):
        cfg = store.load_config()
        go, show = cfg["herdr_key_go"], cfg["herdr_key_focus_window"]
        text = open(HERDR_CONFIG).read() if os.path.exists(HERDR_CONFIG) else ""
        what = f"{go} goes to the session that needs you most, {show} shows the focus screen"
        if KEYS_START in text:
            self.done(f"herdr keys: {what}")
            return
        taken = [k for k in (go, show) if json.dumps(k) in text]
        if taken:
            self.warn(f"herdr keys not added: {', '.join(taken)} already bound in {HERDR_CONFIG}. Set "
                      f"herdr_key_go and herdr_key_focus_window in {store.CONFIG}, then run setup again.")
            return
        if not self.confirm(f"Add two herdr keys? {go}: go to the session that needs you most; "
                            f"{show}: show the focus screen", True):
            self.done("herdr keys: not added (run setup again to add them)")
            return
        block = herdr_keys(command_path(), go, show)
        if os.path.islink(HERDR_CONFIG):  # managed elsewhere (Nix, dotfiles): a write would replace the link
            self.warn(f"{HERDR_CONFIG} is a link, so setup does not change it. Add these keys where it "
                      f"comes from, then reload herdr's config:\n\n{block}")
            self.todo.append("Add the herdr keys that setup printed to your herdr config.")
            return
        self.change(f"add the herdr keys to {HERDR_CONFIG}: {what}")
        self.write(HERDR_CONFIG, add_herdr_keys(text, block)[0])
        if not self.dry_run and herdr.call(path, "server.reload_config") is None:
            self.warn("herdr did not reload its config: reload it in herdr (prefix+shift+r)")

    # 7
    def service(self, kind):
        from . import cli
        if self.dry_run:
            self.change("install the background service" if not os.path.exists(cli.PLIST)
                        else "restart the background service on this code")
            return
        before = _log_size()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_install_daemon()  # bootout + bootstrap: also a restart on new code
        except SystemExit as ex:  # launchctl refused: say so, and go on with the other steps
            self.warn(f"the background service did not start: {ex}")
            self.todo.append("Run `claude-focus install-daemon` and check its message.")
            return
        self.change(f"background service {cli.LABEL} installed and started")
        if kind == terminal.ITERM:
            print("    (macOS may ask once more whether the service may control iTerm2: allow it)")
        deadline = time.time() + SERVICE_WAIT
        while time.time() < deadline:
            m = re.search(r"service connected to (\S+)", _log_since(before))
            if m:
                self.done(f"the service reached {m.group(1)}")
                return
            time.sleep(1)
        self.warn(f"the service did not reach a terminal within {SERVICE_WAIT} s; see {store.LOG} and "
                  f"{os.path.join(store.DIR, 'service.log')}")
        self.todo.append("Check why the service does not reach the terminal (focus.log, service.log).")

    # 8
    def focus_window(self, kind, api, info):
        where = "herdr tab" if kind == terminal.HERDR else "iTerm2 window"
        if info.get("focus_running"):
            self.done("the focus screen is open")
        elif self.dry_run:
            self.change(f"open the focus screen in a new {where}")
        elif api and _open_focus_window(kind, command_path()):
            self.change(f"opened the focus screen in a new {where} (keep it open)")
        else:
            self.warn(f"the focus screen did not open: run `claude-focus` in a new {where}")

    def run(self):
        print("claude-focus setup" + (" (dry run: nothing changes)" if self.dry_run else ""))
        kind = self.checks()
        path = herdr.socket_path()
        api = self.herdr_api(path) if kind == terminal.HERDR else self.iterm_api()
        self.config()
        self.hook()
        self.rule()
        if kind == terminal.HERDR:
            from .restore import focus_pane
            info = {"focus_running": bool(focus_pane())}
            self.done("free terminals: every herdr pane at a bare shell prompt")
            if api:
                self.herdr_integration(path)
                self.herdr_keys(path)
        else:
            info = _iterm_info() if api else {}
            if api:
                self.free_terminals(info)
        self.service(kind)
        self.focus_window(kind, api, info)
        print()
        if self.todo:
            print("Still to do:")
            for t in self.todo:
                print(f"  - {t}")
        else:
            print("Done. Sessions that started before this setup pick up the hook at once and the "
                  "CLAUDE.md rule when they restart.")


def uninstall(purge=False):
    from . import cli
    print("claude-focus uninstall")
    with contextlib.redirect_stdout(io.StringIO()):
        cli.cmd_uninstall_daemon()
    print(f"  - background service {cli.LABEL} stopped and removed")
    try:
        with open(SETTINGS) as f:
            settings = json.load(f)
        if remove_hook(settings):
            _replace(SETTINGS, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
            print(f"  - prompt hook removed from {SETTINGS}")
    except (OSError, ValueError):
        pass
    if os.path.exists(CLAUDE_MD):
        text, changed = remove_rule(open(CLAUDE_MD).read())
        if changed:
            _replace(CLAUDE_MD, text)
            print(f"  - the rule for waits removed from {CLAUDE_MD}")
        elif RULE_TAG in text:
            print(f"  ! {CLAUDE_MD} still mentions claude-focus pending (not added by setup): edit it by hand")
    if os.path.exists(PROFILE_FILE):
        os.remove(PROFILE_FILE)
        print("  - the green iTerm2 profile removed")
    if os.path.exists(HERDR_CONFIG):
        text, changed = remove_herdr_keys(open(HERDR_CONFIG).read())
        if changed:
            _replace(HERDR_CONFIG, text)
            herdr.call(herdr.socket_path(), "server.reload_config")
            print(f"  - the herdr keys removed from {HERDR_CONFIG}")
    if purge:
        shutil.rmtree(store.DIR, ignore_errors=True)
        print(f"  - {store.DIR} removed")
    else:
        print(f"  Kept {store.DIR} (state, config, logs); `claude-focus uninstall --purge` removes it.")
    print("  To remove the program too: uv tool uninstall claude-focus (or pipx uninstall claude-focus)")


# ---- helpers ------------------------------------------------------------------

def _replace(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def _iterm_running():
    from .cli import iterm_running
    return iterm_running()


def _iterm(coro):
    """Run coro(connection) against iTerm2: (True, result) or (False, why).
    The library prints a long help text on failure; it is not shown."""
    import iterm2
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            return True, iterm2.Connection().run_until_complete(coro, False)
    except Exception as ex:  # API off, permission denied, iTerm2 not running
        return False, f"{type(ex).__name__}: {ex}".strip(": ")


def _probe_iterm():
    import iterm2

    async def probe(conn):
        await iterm2.async_get_app(conn)
    return _iterm(probe)


def _iterm_info():
    """Profile names, the default profile's name, and whether a focus screen runs."""
    import iterm2

    from .restore import TUI_FILE

    async def info(conn):
        profiles = await iterm2.PartialProfile.async_query(conn)
        default = await iterm2.PartialProfile.async_get_default(conn)
        app = await iterm2.async_get_app(conn)
        running = False
        try:
            with open(TUI_FILE) as f:
                tui = json.load(f)
            os.kill(int(tui["pid"]), 0)
            running = app.get_session_by_id(tui["session"]) is not None
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return {"profiles": [p.name for p in profiles], "default_profile": default.name,
                "focus_running": running}
    ok, result = _iterm(info)
    return result if ok else {}


def _wait_for_profile(name, seconds=5):
    """iTerm2 loads a new dynamic profile within a moment."""
    import iterm2

    async def names(conn):
        return [p.name for p in await iterm2.PartialProfile.async_query(conn)]
    deadline = time.time() + seconds
    while time.time() < deadline:
        ok, found = _iterm(names)
        if ok and name in found:
            return True
        time.sleep(0.5)
    return False


def _open_focus_window(kind, command):
    if kind == terminal.HERDR:
        import asyncio
        return asyncio.run(herdr.Bridge().open_window(command))
    import iterm2

    async def open_window(conn):
        await iterm2.Window.async_create(conn, command=command)
    return _iterm(open_window)[0]


def _log_size():
    try:
        return os.path.getsize(store.LOG)
    except OSError:
        return 0


def _log_since(offset):
    try:
        with open(store.LOG) as f:
            f.seek(offset)
            return f.read()
    except OSError:
        return ""
