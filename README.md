# claude-focus

One priority queue over all your running Claude Code sessions.

If you run ten or twenty Claude Code sessions at once, a lot of time goes
into finding the one that needs you. claude-focus watches every session in
iTerm2, scores what each one needs from you, and shows them in one list: what
needs you first, then your to-dos, the sessions that wait for something
external, the ones that work, the ones that are done, and your free
terminals. Enter takes you to the selected session's pane. When you answer,
the session works on, and the next one is selected.

![The focus screen](docs/screenshot.png)

It also lets a session wait for something external (a reply, a deploy, a
date) without sitting in your queue. When the time comes or a check command
succeeds, claude-focus types a wake-up message into the session, and Claude
continues by itself.

## Requirements

macOS, [iTerm2](https://iterm2.com) with its Python API on, Python 3.10 or
newer, and [Claude Code](https://claude.com/claude-code) (tested with
2.1.29x to 2.1.306). claude-focus reads Claude Code's own session files,
which are not a public API, so a Claude Code update can break it.

iTerm2 is required. claude-focus finds each session's pane, reads its
screen, types wake-up messages and brings panes to the front through the
iTerm2 Python API, so it does not work with Terminal.app, Ghostty, Warp or
other terminals, and it does not find sessions that run inside tmux.

This is an early release. It runs every day on one Mac with about 20
sessions; expect rough edges elsewhere, and please open an issue.

## Install

First turn on iTerm2's Python API: Settings > General > Magic > Enable
Python API. Then, in iTerm2:

```sh
uv tool install git+https://github.com/nejko/claude-focus
claude-focus setup
```

No uv yet? `brew install uv`, or see [docs.astral.sh/uv](https://docs.astral.sh/uv/).
pipx works too: `pipx install git+https://github.com/nejko/claude-focus`.

`claude-focus setup` does the rest and tells you what it did. You can run it
again at any time; it skips what is done. It:

1. checks for macOS, iTerm2 and `claude`, and connects to iTerm2. The first
   time, macOS asks whether claude-focus may control iTerm2: allow it.
2. asks your name, for the messages to sessions
   (`~/.claude-focus/config.json`).
3. adds the prompt hook to `~/.claude/settings.json` (see "Pending waits").
4. adds a short rule to `~/.claude/CLAUDE.md`, so that sessions know how to
   wait (the text is in
   [claude_focus/claude_md_rule.md](claude_focus/claude_md_rule.md)).
5. asks whether you want free terminals: it adds a green iTerm2 profile and
   lets claude-focus switch panes to it (see "Free terminals").
6. starts the background service, a LaunchAgent that starts at login and
   restarts after a crash or when iTerm2 restarts. It gets the PATH of the
   shell you run setup from: the scorer needs `claude`, and your check
   commands need their tools.
7. opens the focus screen in a new iTerm2 window. Keep that window open.

Before it changes `settings.json` or `CLAUDE.md` the first time, setup keeps
a copy next to it (`.before-claude-focus`). `claude-focus setup --dry-run`
shows what it would do; `--yes` takes the defaults without asking.

The scorer follows the rules in `~/.claude-focus/priorities.md`. Edit them
to say what matters to you.

### Upgrade

`uv tool upgrade claude-focus && claude-focus setup` (setup restarts the
service on the new code).

### Uninstall

`claude-focus uninstall` removes the service, the hook, the
CLAUDE.md rule and the green profile, and keeps your state in
`~/.claude-focus` (`--purge` removes it too). Then
`uv tool uninstall claude-focus`.

### Development

Clone the repo, `python3 -m venv .venv &&
.venv/bin/pip install -e '.[jev]'`, and run `.venv/bin/claude-focus` (or
link `bin/claude-focus` onto your PATH).

## Commands

```sh
claude-focus                  # the focus screen (open it in its own iTerm2 window)
claude-focus setup            # set everything up, or check it again
claude-focus list             # print the queue once
claude-focus pending ...      # from inside a session: wait for something external
claude-focus restore          # after a crash: open the sessions of the last iTerm2 run again
claude-focus reboot           # before a planned restart: save the layout, stop working sessions
claude-focus cost             # scorer calls and spend per day
claude-focus status           # is the service running?
claude-focus uninstall        # undo setup
```

## The focus screen

The sections, top to bottom:

- NEED YOU: the session waits for your answer, ranked by priority.
- YOUR TO-DOS: the session waits for something external and left you a
  task meanwhile (send a draft, sign, pay). Oldest first.
- PENDING: the session waits for a time or a check, or you snoozed it.
  Next wake-up first.
- WORKING: the session is busy, or its background shells still run.
- FREE: nothing is needed; you can close or reuse the terminal.
- AVAILABLE TERMINALS: free iTerm2 panes, ready for a new session (see
  "Free terminals").

Every section uses one grid, so each column is always in the same place: a
lead (in NEED YOU the priority as a number and a bar whose length is the
priority; in PENDING the wake time), an age (how long it has waited for you,
worked or slept; in PENDING the time until it wakes), the name, the folder
the session runs in, a kind (decision, action, check, wait, snoozed, busy,
shells, ...) and the text. The folder column is as wide as the longest
folder on the screen, at most 40 columns; a longer folder keeps its end.
Windows narrower than 110 columns drop the folder column (WORKING then shows
the folder as its text), and below 100 columns also the kind column.

Each section has its own color. A priority of 80 or more is red, of 60 or
more amber, and the rest grey. Alerts are red: a wake-up that could not be
typed, a duplicate window, a session without a pane. What needs you is
bright, PENDING and WORKING are softer, and FREE is grey. The
selected row has a tinted background and a ▌ at the left. The screen uses
24-bit color and follows iTerm2's dark or light theme.

The selection starts on the first row of NEED YOU (else of YOUR TO-DOS).
After Enter, it goes back there. The rows never move when the selection
moves; a long list scrolls to keep the selected row visible. While the focus
window has the keys, its title bar is green.

### Keys

| Key | Action |
|---|---|
| Enter | Go to the selected session; its pane is maximized. If it asks a question, the focus screen comes back when you submit the answers. On a free terminal: choose a folder for a new session there (see "Free terminals") |
| ↑ ↓ (or k j) | Select another row. The list wraps: ↓ on the last row selects the first, ↑ on the first selects the last |
| n | Start a new session: choose a folder for the first free terminal |
| Esc | Select the top of the queue again |
| s | Snooze the selected session: 1 h, 3 h, tomorrow 09:00, Monday 09:00, or a time you type |
| u | Unwait: end a snooze, a pending wait or a "done" mark |
| p | Set a manual priority (0 = back to automatic) |
| d | Done: nothing is needed now; the session comes back when it changes. On a to-do: your part is done |
| r | Score the selected session again |
| R | Restore the windows of the last iTerm2 run (shown only when some are closed) |
| q | Quit |

Two optional global hotkeys (`"hotkeys": true` in the config): ⌃⌥N goes to
the top session from any pane, ⌃⌥F brings the focus screen to the front.

There is no free skip: to move the top session down, snooze it. Its row
shows how many times you did.

After Enter on a session that asks a question (AskUserQuestion), the focus
screen comes back to the front when you submit the answers. It stays in the
session when you press Esc or pick "Chat about this", when the dialog was a
permission prompt, and when you went to another pane or app first.

## Scoring

When a session has waited for 15 s, and you are not in its pane, a scorer
reads the bottom of its screen, its last message and your
`~/.claude-focus/priorities.md`, and rates how much it needs you now:

- a priority from 1 to 100 (five levels, from "nothing needed" to "drop
  everything"),
- a kind: decision, action, login, review, info or nothing,
- the effort in minutes.

The same scorer rates every session, so the numbers can be compared. A
session is scored again every 12 hours. The rank is the score plus 0.15 per
hour of waiting (at most +10), so a neglected session cannot sink forever,
but a medium item never passes a critical one. A session that asks a
question is always in NEED YOU, whatever its score.

There are two scorers (config `scorer`):

- `claude` (the default): a headless `claude -p` call with your own Claude
  Code login, on Sonnet 5.5 (`scorer_model`). It also writes the one-line
  request, a reason, deadlines and wake-up times. About $0.010 per call
  (no CLAUDE.md, memory, MCP servers or thinking; a 5-minute cache).
- `jev`: [TypeSafe](https://typesafe.ai)'s jev model answers typed
  questions with probabilities. About $0.0001 per call, but it needs a
  TypeSafe key (`TYPESAFE_API_KEY` in the environment, or in
  `~/.config/claude-focus/credentials` as `TYPESAFE_API_KEY=...`, mode 600)
  and the jev extra: `uv tool install --force 'claude-focus[jev] @ git+https://github.com/nejko/claude-focus'`.
  jev writes no text, so the one-line request
  comes from the session itself: the open question on the screen, the first
  item under a "Needed from you" style heading, a "nothing needs you"
  sentence, Claude Code's recap line, or the first sentence of the last
  message.

At 150 to 350 calls a day, the claude scorer costs about $45 to $105 per 30
days, jev about $0.50 to $1. `claude-focus cost` shows the real numbers.
When the scorer fails, the focus screen shows the error in red, and new
sessions wait unscored at 50.

## Pending waits

A session that waits for something external runs this from its Bash tool:

```sh
claude-focus pending --until 09:00 --note "launch check"
claude-focus pending --check '<command; exit 0 = it happened>' --every 10 --until "2026-10-02 18:00" --note "the v2.0 tag"
claude-focus pending --check '<command>' --until "+1d" --note "the export" --needs "Review and send the draft"
claude-focus pending --show | --clear
```

The session leaves your queue and shows under PENDING. When the time comes
or the check exits 0, the engine types a one-line wake-up message into the
session's prompt and submits it, so Claude continues by itself. The message
tells Claude to ask you before any irreversible or outward-facing step.

With `--needs`, the session shows under YOUR TO-DOS with that request until
you press d, and the wait still wakes it.

A wait set in a turn that answers your message gets the to-do "Read its
answer to your message", so the answer does not go straight to PENDING
unread. The to-do is done when your keys are in the session's pane after the
turn ended. The same holds when a background task started a later turn and
that turn set the wait.

When you type into a waiting session, the wait ends. Other prompts keep it:
a background task notification, a message from another session, the answer
to a question dialog, and an idle compaction that Claude Code starts by
itself. When the status changes after a wait was set, the engine reads the
transcript, and only a prompt that you typed ends the wait.

The `UserPromptSubmit` hook tells Claude at once that your typing ended its
wait, and gives it the exact command to set the same wait again. Without the
hook, the engine still ends the wait, but Claude does not learn it.
`claude-focus setup` adds it to `~/.claude/settings.json`, and running
sessions pick it up without a restart. By hand, it looks like this:

```json
"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command",
    "command": "/Users/YOU/.local/bin/claude-focus hook prompt-submit", "timeout": 10}]}]
}
```

Claude Code gives the hook no origin field, so the hook recognizes a task
notification or a message from another session by the start of its text.

Safety rules of the waker:

- It types only into an empty prompt: never over a draft, never into an open
  question. It reads the screen and the cursor position. If typing is not
  safe, the session goes into NEED YOU with a red wake-up note (priority at
  least 70), and the wake-up is typed later, when the same session is at an
  empty prompt again and you are not in its pane.
- Only one process runs the waker (the service, or a focus screen when no
  service runs), so a wake-up is never typed twice.
- A check's output is not put into the wake-up message (it can carry text
  from outside, such as an e-mail). `"wake_check_output": true` turns it on.

## Free terminals

A pane whose Claude session has ended is free for the next one. claude-focus
marks it with a green iTerm2 profile, `Available`, and lists it under
AVAILABLE TERMINALS: its folder, the program and the tty. Only panes with
that profile show, and a pane in which a Claude session runs is never free.

Enter on a free terminal, or `n` for the first one, opens a list of folders
for a new session. It holds every folder that Claude Code has run in (Claude
Code's own list in `~/.claude.json`) and the subfolders of your project
folders (`project_roots`), the most recently used first, with the time of
the last session there. Type to search: every word you type must be in the
path, and a folder whose own name starts with the first word comes first.
When no path has what you typed, the letters are an abbreviation of the
folder's name: `clfo` finds `claude-focus`.

- Enter types `cd <folder> && claude` into the terminal (Ctrl-U first, so
  anything typed at its prompt goes away), gives the pane your default
  profile, and takes you there. `new_session_command` sets the command, for
  example `claude --model opus`. It types only into a pane that is still at
  a bare shell.
- Tab goes to the terminal without starting anything.
- Esc goes back to the list of sessions.

If you say yes to free terminals in `claude-focus setup`, it adds the
profile as an iTerm2 dynamic profile (your default profile with a green
background, in
`~/Library/Application Support/iTerm2/DynamicProfiles/claude-focus.json`)
and turns on `auto_profiles`. A profile named `Available` that you made
yourself is used as it is. Then the engine keeps the color true:

- When a Claude session ends and its pane is back at a bare shell (zsh,
  bash, fish, ...), the pane gets the free profile. A pane that runs another
  program after the session (vim, a script) is not free.
- When a Claude session starts in a free pane, the pane gets back your
  default profile (`session_profile`).

You can also mark a pane free by hand: bind a key to "Change Profile" >
Available in iTerm2's key bindings. Config `free_profile` names another
profile.

This is the only part of claude-focus that uses iTerm2 profiles; the colors
of the focus screen need no setup. Without the profile, the section stays
empty, and a switch to a profile that does not exist changes nothing. The
engine switches a profile with iTerm2's `SetProfile` escape sequence, sent
into the pane as program output; nothing shows on the screen.

## Crash recovery

When the Mac crashes, every iTerm2 window and every Claude session in it
stops, and Claude Code deletes the session files of the dead processes. So
claude-focus records the layout itself.

Every 10 s the engine reads each iTerm2 window (frame, display, full
screen) and the Claude session in each pane (id, name, folder, the flags of
its `claude` command, its pending wait). When the layout changed, it appends
a line to `~/.claude-focus/layouts.jsonl` and syncs it to disk.

After the restart, the focus screen says that windows from before are
closed: R shows the list, and y opens it. `claude-focus restore` does the
same from a terminal (`--dry-run` only shows the list). It opens the same
windows on the same displays and types
`cd <folder> && claude <flags> --resume <id>` into each pane's login shell,
so the shell stays when Claude exits. Then it sets the pending waits again.
Other commands that ran in those panes are not run again, and it skips
sessions that run now and folders that are gone. A resume keeps the flags
of the original command that matter: `--model`, `--permission-mode`,
`--add-dir`, `--effort`, and `--dangerously-skip-permissions` if you used it.

### Planned restart

Before you restart, run `claude-focus reboot` in a new
iTerm2 window (`--dry-run` only shows the list). It saves the layout, stops
each working session with Esc, and waits until it stops. After the restart,
the service opens the saved layout by itself, once. A session that was
working is told to continue, a session that asked something asks again, and
an idle session gets no message.

## Configuration

`~/.claude-focus/config.json` overrides the defaults in
`claude_focus/store.py`. For example:

```json
{"user_name": "Ana", "user_role": "a freelance developer", "companies": {"acme": "Acme"}}
```

| Key | Default | What it does |
|---|---|---|
| `user_name` | `"the user"` | How the messages to sessions and the scorer name you |
| `user_role` | `""` | One phrase about you, for the scorer |
| `companies` | `{}` | Folder name prefix to company label, shown with the score |
| `scorer` | `"claude"` | `"claude"` or `"jev"` |
| `scorer_model` | `"claude-sonnet-5-5"` | The model of the claude scorer |
| `jev_model` | `"jev-latest"` | The model of the jev scorer |
| `scorer_parallel` | `4` | Sessions scored at the same time |
| `poll_seconds` | `3` | How often the state is read |
| `aging_points_per_hour`, `aging_max` | `0.15`, `10` | The bonus for waiting |
| `zoom` | `true` | Maximize the pane on Enter |
| `return_after_answer` | `true` | Come back after you answer a question |
| `hotkeys`, `hotkey_go`, `hotkey_focus_window` | `false`, ⌃⌥N, ⌃⌥F | Global hotkeys (only a focus window registers them) |
| `free_profile`, `session_profile` | `"Available"`, `"Default"` | The iTerm2 profiles of free terminals |
| `auto_profiles` | `false` | Switch those profiles by itself (setup turns it on if you want free terminals) |
| `wake_check_output` | `false` | Put a check's output into the wake-up message |
| `new_session_command` | `"claude"` | What a new session in a free terminal runs, after `cd <folder> &&` |
| `project_roots` | `~/Projects`, `~/code`, `~/src`, ... | Folders whose subfolders the folder list offers too |

## Files

All under `~/.claude-focus/` (mode 700):

- `priorities.md`: your rules for the scorer, in plain words. Edit it freely.
- `config.json`: your settings.
- `state.json`: scores, snoozes, manual priorities, pending waits.
- `focus.log`: scores, wake-ups, profile switches, errors.
- `costs.jsonl`: one line per scorer call.
- `layouts.jsonl`: layout snapshots for a restore (3 days, and always the
  whole previous iTerm2 run).
- `engine.json`, `engine.lock`, `service.pid`, `service.log`: which process
  is the engine.

## Privacy and safety

- For each score, the scorer gets the bottom 45 lines of the session's
  screen, its last message (up to 3,500 characters), its folder and name,
  and your `priorities.md`. The claude scorer sends them to Anthropic
  through your Claude Code; jev sends them to TypeSafe. claude-focus sends
  nothing else anywhere.
- It types into your terminals only for wake-up messages (only into an
  empty prompt), the restore commands, Esc on `reboot`, the profile
  escape sequence, and the command of a new session that you start from the
  folder list.
- The background service runs your check commands with your shell and your
  PATH.
- It reads `~/.claude/sessions/`, the transcripts in `~/.claude/projects/`
  and the list of folders in `~/.claude.json`, and never writes them.

## How it works

One process is the engine: it scores, runs the waker, takes the layout
snapshots and switches profiles. It holds `~/.claude-focus/engine.lock`, so
there is never a second one. Normally the engine is the background service,
and every focus screen is a view of the shared state (its header says
`service`). Without the service, the first focus screen is the engine.

Every 3 s it reads `~/.claude/sessions/<pid>.json`, the data behind
`claude agents --json`: `busy`, `waiting` (a question or a permission
prompt) or `idle`. It finds each session's pane by its tty through the
iTerm2 Python API, including panes hidden by a maximized pane.

If two processes run the same conversation (a `--resume` while the old
window is still open), both rows show DUPLICATE. Close one of them, because
both write to one transcript.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests
```

The tests use a scratch folder, never your `~/.claude-focus`.

## License

MIT. See [LICENSE](LICENSE).
