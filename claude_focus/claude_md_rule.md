# Waiting on something external: claude-focus pending

I work through my Claude Code sessions with claude-focus, a priority queue. When your task must wait for something external (a reply, a deploy, a date) and nothing is needed from me until then, do not leave the session idle in my queue. At the end of the turn, run one of these in Bash:

   claude-focus pending --until "<HH:MM | +2h | YYYY-MM-DD HH:MM>" --note "<what you wait for>"
   claude-focus pending --check '<command; exit 0 = it happened>' --every <minutes> --until "<latest time>" --note "<what you wait for>"

The session then leaves my queue. When the time comes or the check succeeds, claude-focus types a wake-up message into this session, and you continue from there. Make the check read-only and fast (for example a mail search with a command-line client, a curl to a status URL, or `git ls-remote`). In the final message, tell me what you wait for and when you wake.

If you wait for something external AND I must also do something meanwhile (for example review and send a draft you wrote, sign, pay, log in), add `--needs "<what I must do>"`. The session then shows under YOUR TO-DOS in my queue with that request until I mark it done, and the wait still wakes you. Never hide a request to me in a plain wait. If you need only me and wait for nothing external, do not run pending; ask me.

If you wait for something external AND need my answer to a question, keep the wait and queue the question instead of opening the AskUserQuestion dialog: an open dialog blocks the session until I answer, so the wake-up would wait too.

   claude-focus ask --question "<the question, standing alone>" --option "<label>::<what happens>" --option "..." --default "<what you do meanwhile>" --costly-after "<when waiting starts to cost: HH:MM | +2h>"

(up to four questions with `--json '{"questions": [...]}'`, as for AskUserQuestion). It shows under YOUR TO-DOS in my queue. When I come to it, claude-focus types `[claude-focus ask] ... is here for your question` into this session: then ask it with AskUserQuestion (`claude-focus ask --show` prints it).

If the next step waits for other people (ratings on a page, a reply to a mail I send, comments on a document) and you can see when they arrive (form submissions, the mail thread, the document), do not end with "tell me when they have answered". Set a check for it instead, with `--needs` for my part (for example "Share the link with the raters").
