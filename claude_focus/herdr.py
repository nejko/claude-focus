"""herdr bridge (its socket API, https://herdr.dev): find a Claude session's
pane, read its screen, type into it, bring it to the front, and show the
queue in herdr's sidebar. The same methods as iterm.Bridge.

herdr knows each pane's foreground processes, and its Claude Code hook
(`herdr integration install claude`) reports each pane's session id, so a
session's pane is found by its process, else by its session id: no tty."""
import asyncio
import json
import os
import re
import socket
import subprocess
import time

from . import store
from .screen import EMPTY, OTHER, clean, prompt_state, strip_ansi
from .terminal import SHELLS

PROTOCOL = 22             # the API version this was written for (herdr 0.9.5)
DEFAULT_SOCKET = os.path.expanduser("~/.config/herdr/herdr.sock")
TIMEOUT = 5               # seconds for one request
GONE_AFTER = 10           # failed refreshes in a row before the bridge gives up (herdr quit)
FRONT_SECONDS = 1.0       # how long the answer "is herdr's terminal the front app" is kept
THEME_SECONDS = 30
FOCUS_RECHECK = 2.0       # seconds: switching apps sends herdr no event
SHELL_WAIT = 5.0          # seconds a new tab may take to start its shell
SOURCE = "claude-focus"   # the metadata source of the sidebar tokens
TOKEN_TTL_MS = 10 * 60 * 1000  # the tokens vanish if claude-focus stops
TOKEN_REFRESH = 4 * 60    # seconds: tokens are sent again before they expire
ASK_CELLS = 60            # the longest request shown in the sidebar


def socket_path(cfg=None):
    """herdr's API socket: config "herdr_socket", else $HERDR_SOCKET_PATH (set
    in every herdr pane), else the default session's."""
    cfg = cfg or store.load_config()
    return os.path.expanduser(cfg.get("herdr_socket") or os.environ.get("HERDR_SOCKET_PATH") or DEFAULT_SOCKET)


class Error(Exception):
    """herdr refused a request, or no server answered."""


def request_now(path, method, params=None, timeout=TIMEOUT):
    """One request on its own connection, blocking: herdr answers one request
    per connection and then closes it. The request goes out right after the
    connect: from asyncio's streams, which send it a moment later, herdr
    0.9.5 answers only after about 100 ms (measured 2026-10-21), from here
    in under 1 ms."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(path)
            s.sendall((json.dumps({"id": SOURCE, "method": method, "params": params or {}}) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(1 << 20)
                if not chunk:
                    break
                buf += chunk
    except OSError as ex:
        raise Error(f"herdr {method}: no server at {path} ({type(ex).__name__})") from None
    try:
        reply = json.loads(buf)
    except ValueError:
        raise Error(f"herdr {method}: no answer") from None
    if "error" in reply:
        e = reply["error"] or {}
        raise Error(f"herdr {method}: {e.get('code')}: {e.get('message')}")
    return reply.get("result") or {}


async def request(path, method, params=None, timeout=TIMEOUT):
    """request_now in a worker thread, so requests run side by side."""
    return await asyncio.get_event_loop().run_in_executor(None, request_now, path, method, params, timeout)


def ping(path, timeout=1.0):
    """herdr's answer to ping ({"version", "protocol", ...}), or None when no
    server answers at path. Blocking: for detection and setup."""
    try:
        return request_now(path, "ping", timeout=timeout)
    except Error:
        return None


def call(path, method, params=None, timeout=TIMEOUT):
    """One request, blocking (setup and uninstall): the result, or None."""
    try:
        return request_now(path, method, params, timeout)
    except Error:
        return None


def front_is_herdr(ps_out=None, front_pid=None):
    """Is the front app the terminal that shows herdr (an app with a herdr
    client among its child processes)? herdr's focused pane gets the user's
    keys only then. None when macOS does not say."""
    try:
        if front_pid is None:
            asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=2).stdout.strip()
            info = subprocess.run(["lsappinfo", "info", "-only", "pid", asn],
                                  capture_output=True, text=True, timeout=2).stdout
            m = re.search(r"\bpid = (\d+)", info)
            if not m:
                return None
            front_pid = int(m.group(1))
        if ps_out is None:
            ps_out = subprocess.run(["ps", "-axo", "pid=,ppid=,args="], capture_output=True, text=True,
                                    timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    parent, clients = {}, []
    for line in ps_out.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        pid = int(parts[0])
        parent[pid] = int(parts[1])
        if os.path.basename(parts[2]) == "herdr" and parts[3:4] != ["server"]:
            clients.append(pid)  # `herdr` or `herdr --session x`: a client that shows panes
    for pid in clients:
        for _ in range(64):
            if pid == front_pid:
                return True
            pid = parent.get(pid, 1)
            if pid <= 1:
                break
    return False


def appearance():
    """macOS's appearance: "dark" or "light"."""
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return "dark"
    return "dark" if "dark" in out.lower() else "light"


class Bridge:
    """Pane ids are herdr's ("w9:p1"). A pane id changes when its pane moves
    to another workspace, so every poll looks them up again (refresh)."""
    name, label = "herdr", "herdr"
    profiles = restores = hotkeys = False  # iTerm2's: colored profiles, window restore, global hotkeys
    sidebar = True                         # the queue in herdr's sidebar (report)

    def __init__(self, path=None):
        self.path = path or socket_path()
        self.version = None
        self.panes = {}         # pane id -> pane.list row, plus its foreground processes ("procs")
        self.by_pid = {}        # pid of a foreground process -> pane id
        self.by_session = {}    # Claude session id that herdr's hook reported -> [pane ids]
        self.failures = 0
        self.front = (0, None)  # (Unix s, front_is_herdr())
        self.dark = (0, "dark")
        self.reported = {}      # pane id -> (tokens, Unix s) sent to the sidebar

    async def call(self, method, params=None):
        return await request(self.path, method, params)

    async def start(self):
        pong = await self.call("ping")
        self.version = pong.get("version")
        if pong.get("protocol") != PROTOCOL:
            store.log(f"herdr {self.version} speaks API protocol {pong.get('protocol')}; claude-focus "
                      f"was written for {PROTOCOL}, so some calls may fail")

    def own_pane(self):
        """The herdr pane this process runs in ("" outside herdr)."""
        return os.environ.get("HERDR_PANE_ID", "")

    def _panes_now(self):
        """Every pane (pane.list) with its foreground processes: one request
        after the other, about 4 ms for 20 panes. Side by side, herdr 0.9.5
        answers them in about 100 ms."""
        rows = request_now(self.path, "pane.list")["panes"]
        for r in rows:
            try:
                info = request_now(self.path, "pane.process_info", {"pane_id": r["pane_id"]})
            except Error:
                info = {}  # the pane closed meanwhile
            info = info.get("process_info") or {}
            r["procs"], r["shell_pid"] = info.get("foreground_processes") or [], info.get("shell_pid")
        return rows

    async def refresh(self):
        """Learn every pane and its foreground processes. After GONE_AFTER
        failures in a row, herdr has quit: SystemExit, and launchd starts the
        service again, which waits for a terminal."""
        loop = asyncio.get_event_loop()
        try:
            rows, _ = await asyncio.gather(loop.run_in_executor(None, self._panes_now), self._front())
        except (Error, KeyError):
            self.failures += 1
            if self.failures >= GONE_AFTER:
                raise SystemExit(f"herdr does not answer at {self.path}")
            raise
        self.failures = 0
        panes, by_pid, by_session = {}, {}, {}
        for r in rows:
            panes[r["pane_id"]] = r
            for p in r["procs"]:
                by_pid[p["pid"]] = r["pane_id"]
            sid = (r.get("agent_session") or {}).get("value")
            if sid:
                by_session.setdefault(sid, []).append(r["pane_id"])
        self.panes, self.by_pid, self.by_session = panes, by_pid, by_session

    def pane_of(self, s):
        """The pane whose foreground runs the session's process; else the one
        pane that herdr's hook named for its session id (Claude Code
        suspended with Ctrl-Z, say). Two processes of one conversation (a
        --resume while the old one runs) are told apart by process."""
        pane = self.by_pid.get(s.get("pid"))
        if pane:
            return pane
        panes = self.by_session.get(s.get("sessionId")) or []
        return panes[0] if len(panes) == 1 else None

    async def _front(self, fresh=False):
        when, front = self.front
        if fresh or time.time() - when > FRONT_SECONDS:
            front = await asyncio.get_event_loop().run_in_executor(None, front_is_herdr)
            self.front = (time.time(), front)
        return front

    def user_pane(self):
        """The pane that gets the user's keys: herdr's focused pane, while the
        terminal that shows herdr is the front app (as of the last refresh)."""
        if self.front[1] is False:
            return None
        return next((p for p, r in self.panes.items() if r.get("focused")), None)

    async def has_focus(self, pane):
        """True when herdr's terminal is the front app and the pane gets the keys."""
        try:
            row = (await self.call("pane.get", {"pane_id": pane}))["pane"]
        except (Error, KeyError):
            return False
        return bool(row.get("focused")) and await self._front(fresh=True) is not False

    async def pane_info(self):
        """(pane id, where, profile, folder, program) for every pane of the last
        refresh(), in herdr's order. `where` is workspace:tab, plus .n when the
        tab has more panes ("metaos:3.2"). herdr has no profiles (None). The
        program is the agent herdr recognized, else the foreground process."""
        try:
            spaces = {w["workspace_id"]: w["label"] for w in (await self.call("workspace.list"))["workspaces"]}
            tabs = {t["tab_id"]: t["label"] for t in (await self.call("tab.list"))["tabs"]}
        except (Error, KeyError):
            spaces, tabs = {}, {}
        per_tab = {}
        for r in self.panes.values():
            per_tab.setdefault(r["tab_id"], []).append(r["pane_id"])
        out = []
        for pane, r in self.panes.items():
            where = f"{spaces.get(r['workspace_id'], r['workspace_id'])}:{tabs.get(r['tab_id'], r['tab_id'])}"
            if len(per_tab[r["tab_id"]]) > 1:
                where += f".{per_tab[r['tab_id']].index(pane) + 1}"
            job = r.get("agent") or (r["procs"][0]["name"] if r["procs"] else "")
            out.append((pane, where, None, r.get("foreground_cwd") or r.get("cwd") or "", job))
        return out

    @staticmethod
    def is_free(profile, job, cfg):
        """A free terminal: a pane at a bare shell, with no agent in it."""
        return (job or "").lstrip("-") in SHELLS

    async def screen_text(self, pane, max_lines=45):
        try:
            read = (await self.call("pane.read", {"pane_id": pane, "source": "visible"}))["read"]
        except (Error, KeyError):
            return ""
        lines = [clean(x) for x in read["text"].splitlines()]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines[-max_lines:])

    async def prompt_state(self, pane):
        try:
            read = (await self.call("pane.read", {"pane_id": pane, "source": "visible", "format": "ansi",
                                                  "strip_ansi": False}))["read"]
        except (Error, KeyError):
            return OTHER
        return prompt_state(strip_ansi(read["text"]), ansi=read["text"])

    async def send_prompt(self, pane, text):
        """Type one line into an idle Claude Code prompt and submit it.
        Returns (True, "") or (False, why). Types only into an EMPTY input box:
        never over a draft, never into an open question."""
        if pane not in self.panes:
            return False, "no herdr pane"
        state = await self.prompt_state(pane)
        if state != EMPTY:
            return False, "the prompt has a draft" if state == "draft" else "the screen shows no empty prompt"
        text = " ".join(text.split())  # one line: a newline would submit early
        try:
            await self.call("pane.send_text", {"pane_id": pane, "text": text})
            await asyncio.sleep(0.6)
            await self.call("pane.send_keys", {"pane_id": pane, "keys": ["enter"]})
        except Error as ex:
            return False, str(ex)
        return True, ""

    async def send_text(self, pane, text):
        """Type text as it is: control characters (\\x15, \\r) act as keys."""
        try:
            await self.call("pane.send_text", {"pane_id": pane, "text": text})
            return True
        except Error as ex:
            store.log(f"herdr: typing into {pane} failed: {ex}")
            return False

    async def go(self, pane, zoom=True):
        """Bring the pane to the front, in whatever workspace and tab. With
        zoom, it fills its tab (herdr's zoom follows the focus in a tab)."""
        try:
            await self.call("pane.focus", {"pane_id": pane})
            if zoom:
                await self.call("pane.zoom", {"pane_id": pane, "mode": "on"})
        except Error as ex:
            store.log(f"herdr: go to {pane} failed: {ex}")
            return False
        return True

    async def activate_own(self):
        """Bring the pane that runs claude-focus to the front."""
        own = self.own_pane()
        if own:
            await self.go(own, zoom=False)

    async def theme(self):
        """herdr has no theme to ask for: macOS's appearance."""
        when, theme = self.dark
        if time.time() - when > THEME_SECONDS:
            theme = await asyncio.get_event_loop().run_in_executor(None, appearance)
            self.dark = (time.time(), theme)
        return theme

    async def open_window(self, command):
        """A new tab in the workspace the user looks at, running command."""
        try:
            made = await self.call("tab.create", {"focus": True, "cwd": os.path.expanduser("~")})
            pane = made["root_pane"]["pane_id"]
            end = time.time() + SHELL_WAIT
            while time.time() < end:  # type once the shell runs: before that, its startup may eat the keys
                info = (await self.call("pane.process_info", {"pane_id": pane}))["process_info"]
                procs = info.get("foreground_processes") or []
                if procs and procs[0]["pid"] == info.get("shell_pid"):
                    break
                await asyncio.sleep(0.2)
            await asyncio.sleep(0.3)
            await self.call("pane.send_text", {"pane_id": pane, "text": command + "\r"})
            return True
        except (Error, KeyError) as ex:
            store.log(f"herdr: no new tab for {command!r}: {ex!r}")
            return False

    async def notify(self, text):
        """A herdr toast: a herdr key runs `claude-focus go` detached, where a
        printed line reaches no one."""
        try:
            await self.call("notification.show", {"title": "claude-focus", "body": text})
        except Error:
            pass

    async def _events(self, types, every=None):
        """herdr's events of these types as they come; None every `every`
        seconds without one."""
        try:
            reader, writer = await asyncio.open_unix_connection(self.path, limit=1 << 20)
        except OSError as ex:
            raise Error(f"no herdr server at {self.path} ({type(ex).__name__})") from None
        try:
            writer.write((json.dumps({"id": SOURCE, "method": "events.subscribe", "params": {
                "subscriptions": [{"type": t} for t in types]}}) + "\n").encode())
            await writer.drain()
            first = json.loads(await reader.readline() or b"{}")
            if "error" in first or not first:
                raise Error(f"herdr events.subscribe: {first.get('error')}")
            while True:
                try:
                    line = await (asyncio.wait_for(reader.readline(), every) if every else reader.readline())
                except asyncio.TimeoutError:
                    yield None
                    continue
                if not line:
                    raise Error("herdr closed the event stream")
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
        finally:
            writer.close()

    async def watch_focus(self, pane, on_change):
        """Call on_change(True or False) when the pane may have got or lost the
        keys: on herdr's focus events, and every FOCUS_RECHECK s for the front
        app (switching apps sends herdr nothing)."""
        async for _ in self._events(["pane.focused", "tab.focused", "workspace.focused"], every=FOCUS_RECHECK):
            on_change(await self.has_focus(pane))

    async def watch_events(self, on_change):
        """Call on_change() when a pane opens, closes, changes focus or gets an
        agent, so the queue follows at once instead of at the next poll."""
        async for _ in self._events(["pane.created", "pane.closed", "pane.exited", "pane.agent_detected",
                                     "pane.focused"]):
            on_change()

    async def report(self, marks):
        """Put each session's place in the queue into herdr's pane metadata,
        for the sidebar ($cf_state, $cf_priority, $cf_ask in herdr's
        ui.sidebar.agents rows). marks: {pane: (section, priority, request)}.
        Tokens are sent when they change, and again before their TTL ends; a
        pane that left the queue gets its tokens cleared."""
        now = time.time()
        for pane, mark in marks.items():
            tokens = sidebar_tokens(*mark)
            sent = self.reported.get(pane)
            if sent and sent[0] == tokens and now - sent[1] < TOKEN_REFRESH:
                continue
            if await self._tokens(pane, tokens, TOKEN_TTL_MS):
                self.reported[pane] = (tokens, now)
        for pane in [p for p in self.reported if p not in marks]:
            if pane in self.panes:
                await self._tokens(pane, {k: None for k in self.reported[pane][0]}, None)
            del self.reported[pane]

    async def _tokens(self, pane, tokens, ttl_ms):
        params = {"pane_id": pane, "source": SOURCE, "tokens": tokens, "seq": time.time_ns()}
        if ttl_ms:
            params["ttl_ms"] = ttl_ms
        try:
            await self.call("pane.report_metadata", params)
            return True
        except Error as ex:
            store.log(f"herdr: sidebar tokens for {pane} not sent: {ex}")
            return False


def sidebar_tokens(section, priority, ask):
    """The sidebar tokens of one queue row."""
    ask = " ".join((ask or "").split())
    return {"cf_state": section, "cf_priority": f"{priority:.0f}" if priority is not None else "",
            "cf_ask": ask if len(ask) <= ASK_CELLS else ask[:ASK_CELLS - 1] + "…"}
