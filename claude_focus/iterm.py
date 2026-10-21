"""iTerm2 bridge (Python API): find a Claude session's pane by tty, read its
screen, bring it to the front with its pane maximized, and catch hotkeys.
herdr.py has the same methods for herdr."""
import asyncio
import json
import os
import re
import subprocess

import iterm2

from . import store
from .screen import EMPTY, clean, prompt_state

MAXIMIZE = iterm2.MainMenu.View.MAXIMIZE_ACTIVE_PANE.value.identifier


def own_pane():
    """The iTerm2 session id of the pane this process runs in ("" outside iTerm2)."""
    return os.environ.get("ITERM_SESSION_ID", "").split(":")[-1]


class Bridge:
    name, label = "iterm", "iTerm2"
    profiles = restores = hotkeys = True  # colored profiles, window restore (restore.py), global hotkeys

    def __init__(self, connection):
        self.conn = connection
        self.app = None
        # iterm session_id -> {"tty", "tab_id", "session"}. A maximized pane
        # hides the other panes of its tab from the API, so panes are
        # remembered after the first time they are seen.
        self._known = {}
        self.panes = {}  # tty -> iterm session id (the last refresh)
        self.ttys = {}   # (pid, session id) -> tty, so ps runs once for each process

    async def start(self):
        self.app = await iterm2.async_get_app(self.conn)

    def own_pane(self):
        return own_pane()

    async def refresh(self):
        self.panes = await self.pane_map()

    def tty_of(self, s):
        if "tty" not in s:
            proc = (s["pid"], s["sessionId"])  # a process keeps its tty
            if proc not in self.ttys:
                try:
                    tty = subprocess.run(["ps", "-o", "tty=", "-p", str(s["pid"])],
                                         capture_output=True, text=True).stdout.strip()
                except OSError:
                    tty = ""
                if not tty:
                    s["tty"] = ""
                    return ""
                self.ttys[proc] = tty
            s["tty"] = self.ttys[proc]
        return s["tty"]

    def pane_of(self, s):
        """The pane of a session: the one with the tty of its process."""
        return self.panes.get(self.tty_of(s))

    def user_pane(self):
        """The active pane of the front iTerm window, while iTerm2 is the active app."""
        app = self.app
        w = app.current_window if app else None
        if not w or app.app_active is False or not w.current_tab:
            return None
        cur = w.current_tab.current_session
        return cur.session_id if cur is not None else None

    @staticmethod
    def is_free(profile, job, cfg):
        """A free terminal: a pane that shows the green profile (config "free_profile")."""
        return profile == cfg["free_profile"]

    def _visible(self):
        for w in self.app.terminal_windows:
            for t in w.tabs:
                for s in t.sessions:
                    yield t, s

    async def pane_map(self):
        """tty ('ttys004') -> iterm session_id, for every pane, hidden ones too."""
        seen = set()
        for t, s in self._visible():
            seen.add(s.session_id)
            k = self._known.get(s.session_id)
            if k is None:
                try:
                    tty = (await s.async_get_variable("tty") or "").replace("/dev/", "")
                except Exception:  # pane closing while we ask
                    continue
                k = self._known[s.session_id] = {"tty": tty}
            k["tab_id"], k["session"] = t.tab_id, s
        # A maximized pane hides the other panes of its tab from the app model;
        # the raw session list still names them (minimized_sessions).
        try:
            raw = await iterm2.rpc.async_list_sessions(self.conn)
            for win in raw.list_sessions_response.windows:
                for tab in win.tabs:
                    for ms in tab.minimized_sessions:
                        sid = ms.unique_identifier
                        seen.add(sid)
                        k = self._known.get(sid)
                        if k is None:
                            s = self.app.get_session_by_id(sid)
                            if s is None:
                                continue
                            tty = (await s.async_get_variable("tty") or "").replace("/dev/", "")
                            k = self._known[sid] = {"tty": tty, "session": s}
                        k["tab_id"] = tab.tab_id
        except Exception as ex:  # never let discovery break the poll
            store.log(f"hidden pane discovery failed: {ex!r}")
        for sid in list(self._known):
            if sid in seen:
                continue
            k = self._known[sid]
            alive = self.app.get_tab_by_id(k.get("tab_id")) is not None
            if alive:
                try:
                    alive = bool(await k["session"].async_get_variable("tty"))
                except Exception:
                    alive = False
            if not alive:
                del self._known[sid]
        return {k["tty"]: sid for sid, k in self._known.items() if k["tty"]}

    def known_panes(self):
        """(iterm session_id, tty, tab_id) for every pane of the last pane_map()."""
        return [(sid, k["tty"], k.get("tab_id")) for sid, k in self._known.items() if k["tty"]]

    async def pane_info(self):
        """(iterm session_id, tty, profile name, folder, program) for every pane
        of the last pane_map(), oldest pane (lowest tty) first. One request per
        pane, all at once: about 2 ms for 24 panes. The profile is the one the
        pane shows now (after a "Change profile" key), which the profile
        object of the API does not say."""
        names = ["profileName", "path", "jobName"]
        panes = self.known_panes()
        replies = await asyncio.gather(*[iterm2.rpc.async_variable(self.conn, sid, [], names)
                                         for sid, _, _ in panes], return_exceptions=True)
        out = []
        for (sid, tty, _), reply in zip(panes, replies):
            try:
                values = [json.loads(v) for v in reply.variable_response.values]
            except (AttributeError, TypeError, ValueError):
                continue  # the pane closed while we asked
            if len(values) == len(names):
                out.append((sid, tty, *values))
        out.sort(key=lambda p: int(re.sub(r"\D", "", p[1]) or 0))
        return out

    async def set_profile(self, sid, name):
        """Switch a pane to the profile `name`, as a "Change profile" key binding
        do. Session.async_set_profile copies every property and iTerm2 refuses
        it (REQUEST_MALFORMED), so this sends iTerm2's SetProfile escape as
        program output: nothing shows on the screen."""
        s = self.session(sid)
        if s is None:
            return False
        await s.async_inject(f"\033]1337;SetProfile={name}\a".encode())
        return True

    def session(self, sid):
        k = self._known.get(sid)
        return k["session"] if k else None

    async def screen_text(self, sid, max_lines=45):
        s = self.session(sid)
        if s is None:
            return ""
        try:
            contents = await s.async_get_screen_contents()
        except Exception:
            return ""
        lines = [clean(contents.line(i).string) for i in range(contents.number_of_lines)]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines[-max_lines:])

    async def prompt_state(self, sid):
        s = self.session(sid)
        try:
            c = await s.async_get_screen_contents()
        except Exception:
            return "other"
        lines = [clean(c.line(i).string) for i in range(c.number_of_lines)]
        cur = c.cursor_coord
        return prompt_state("\n".join(lines), cursor=(cur.x, cur.y))

    async def send_prompt(self, sid, text):
        """Type one line into an idle Claude Code prompt and submit it.
        Returns (True, "") or (False, why). Types only into an EMPTY input box:
        never over a draft, never into an open question."""
        s = self.session(sid)
        if s is None:
            return False, "no iTerm pane"
        state = await self.prompt_state(sid)
        if state != EMPTY:
            return False, "the prompt has a draft" if state == "draft" else "the screen shows no empty prompt"
        text = " ".join(text.split())  # one line: a newline would submit early
        await s.async_send_text(text)
        await asyncio.sleep(0.6)
        await s.async_send_text("\r")
        return True, ""

    async def send_text(self, sid, text):
        """Type text as it is: control characters (\\x15, \\r) act as keys."""
        s = self.session(sid)
        if s is None:
            return False
        await s.async_send_text(text)
        return True

    async def open_window(self, command):
        """A new iTerm2 window running command."""
        await iterm2.Window.async_create(self.conn, command=command)
        return True

    async def _maximized(self):
        state = await iterm2.MainMenu.async_get_menu_item_state(self.conn, MAXIMIZE)
        return state.checked

    async def _toggle_maximize(self):
        await iterm2.MainMenu.async_select_menu_item(self.conn, MAXIMIZE)
        await asyncio.sleep(0.2)

    async def go(self, sid, zoom=True):
        """Bring the pane to the front. With zoom, the pane fills its window
        (iTerm's Maximize Active Pane); another maximized pane in the same
        tab is restored first."""
        k = self._known.get(sid)
        if not k:
            return False
        tab = self.app.get_tab_by_id(k["tab_id"])
        if tab is None:
            return False
        await self.app.async_activate(raise_all_windows=False, ignoring_other_apps=True)
        await tab.async_activate(order_window_front=True)
        await asyncio.sleep(0.15)
        if zoom and await self._maximized() and tab.current_session.session_id != sid:
            await self._toggle_maximize()
        s = self.app.get_session_by_id(sid) or k["session"]
        await s.async_activate(select_tab=True, order_window_front=True)
        await asyncio.sleep(0.15)
        if zoom and len(tab.sessions) > 1 and not await self._maximized():
            await self._toggle_maximize()
        return True

    async def theme(self):
        """iTerm2's theme now: "dark", "light", or with more words ("light highContrast")."""
        try:
            return await self.app.async_get_variable("effectiveTheme") or ""
        except Exception:
            return ""

    async def has_focus(self, sid):
        """True when iTerm2 is the active app and the pane gets the keys."""
        await self.app.async_refresh_focus()
        w = self.app.current_window
        s = w.current_tab.current_session if w and w.current_tab else None
        return bool(self.app.app_active) and s is not None and s.session_id == sid

    async def activate_own(self):
        """Bring the window that runs claude-focus to the front."""
        sid = own_pane()
        s = self.app.get_session_by_id(sid) if sid else None
        if s:
            await self.app.async_activate(raise_all_windows=False, ignoring_other_apps=True)
            await s.async_activate(select_tab=True, order_window_front=True)

    async def watch_focus(self, sid, on_change):
        await watch_focus(self.conn, self, sid, on_change)

    async def watch_hotkeys(self, bindings):
        await watch_hotkeys(self.conn, bindings)

    async def watch_events(self, on_change):
        """Nothing to watch: the poll finds new and closed panes."""

    async def report(self, marks):
        """iTerm2 has no place for the queue but the focus screen."""

    async def notify(self, text):
        """The caller prints text: `claude-focus go` runs in a terminal here."""


async def watch_focus(conn, bridge, sid, on_change):
    """Call on_change(True or False) on every focus change of iTerm2: does
    the pane `sid` get the keys now?"""
    async with iterm2.FocusMonitor(conn) as mon:
        while True:
            await mon.async_get_next_update()
            on_change(await bridge.has_focus(sid))


def parse_hotkey(spec):
    """'ctrl+option+n' -> KeystrokePattern."""
    mods = {"ctrl": iterm2.Modifier.CONTROL, "control": iterm2.Modifier.CONTROL,
            "option": iterm2.Modifier.OPTION, "alt": iterm2.Modifier.OPTION,
            "cmd": iterm2.Modifier.COMMAND, "command": iterm2.Modifier.COMMAND,
            "shift": iterm2.Modifier.SHIFT}
    parts = [p.strip().lower() for p in spec.split("+")]
    key = parts[-1]
    keycode = {"space": iterm2.Keycode.SPACE, "return": iterm2.Keycode.RETURN,
               "enter": iterm2.Keycode.RETURN}.get(key) or getattr(iterm2.Keycode, "ANSI_" + key.upper())
    p = iterm2.KeystrokePattern()
    p.required_modifiers = [mods[m] for m in parts[:-1]]
    p.keycodes = [keycode]
    return p, set(p.required_modifiers), keycode


async def watch_hotkeys(conn, bindings):
    """bindings: {spec: async callback}. Swallows the keys in every session."""
    parsed = [(parse_hotkey(spec), cb) for spec, cb in bindings.items()]
    patterns = [p for (p, _, _), _ in parsed]
    async with iterm2.KeystrokeFilter(conn, patterns):
        async with iterm2.KeystrokeMonitor(conn) as mon:
            while True:
                ks = await mon.async_get()
                mods = set(ks.modifiers) - {iterm2.Modifier.FUNCTION, iterm2.Modifier.NUMPAD}
                for spec, ((_, need, keycode), cb) in zip(bindings, parsed):
                    if ks.keycode == keycode and mods == need:
                        store.log(f"hotkey {spec}")
                        asyncio.ensure_future(cb())
