"""Poll as soon as something changes, not up to poll_seconds later.

Claude Code rewrites ~/.claude/sessions/<pid>.json in place when a session's
status changes, and its folder sees nothing then (2026-10-24: in 60 s, both
status changes of 16 sessions came as writes to their own files, none to
the folder). So kqueue watches each session file, and the folders for files
that come and go: the sessions folder, and for a focus window
~/.claude-focus, where the service replaces state.json with each score.
The poll every poll_seconds stays and finds whatever this misses."""
import os
import select
import threading

from . import store

RESCAN_S = 30  # look for new session files at least this often


def start(on_change, sessions_dir, folders=()):
    """Watch in a daemon thread and call on_change(path) from that thread for
    each batch of changes; path is a watched folder when only its entries
    changed, else the session folder. Returns False without kqueue."""
    if not hasattr(select, "kqueue"):
        return False
    threading.Thread(target=_guarded, args=(on_change, sessions_dir, tuple(folders)),
                     daemon=True, name="watch").start()
    return True


def _guarded(*args):
    """A traceback from this thread would land on the focus screen; the
    poll goes on without the watcher."""
    try:
        _run(*args)
    except Exception as ex:
        store.log(f"watch stopped: {ex!r}")


def _run(on_change, sessions_dir, folders):
    kq = select.kqueue()
    fds = {}  # path -> fd
    paths = {}  # fd -> path
    gone = select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME

    def watch(path):
        try:
            fd = os.open(path, os.O_EVTONLY)
        except OSError:
            return
        fds[path], paths[fd] = fd, path
        kq.control([select.kevent(fd, filter=select.KQ_FILTER_VNODE, flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                                  fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | gone)], 0)

    def drop(path):
        fd = fds.pop(path)
        paths.pop(fd, None)
        os.close(fd)  # closing removes its kqueue event

    def sync():
        try:
            want = {os.path.join(sessions_dir, n) for n in os.listdir(sessions_dir) if n.endswith(".json")}
        except OSError:
            want = set()
        want |= {sessions_dir, *folders}
        for path in set(fds) - want:
            drop(path)
        for path in want - set(fds):
            watch(path)

    sync()
    while True:
        events = kq.control(None, 64, RESCAN_S)
        changed = set()
        for e in events:
            path = paths.get(e.ident)
            if path is None:
                continue
            if e.fflags & gone:
                drop(path)  # sync() watches the file that took its name, if any
            changed.add(path if path in folders else sessions_dir)
        sync()
        for path in changed:
            on_change(path)
