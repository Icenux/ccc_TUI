# Usage limits & blocked queues

Your Claude subscription has usage limits: a **5-hour** window and a
**weekly** one. ccc reads your real usage — the same numbers as
[Analytics](analytics.md) — and plans around them, so queued work never
fails just because a limit ran out.

## When a limit runs out

- Before ccc sends a task it checks your usage. While a window is used
  up it doesn't even start; it waits for that window's reset.
- If Claude hits the limit in the middle of a task, ccc waits for the
  reset, then continues. If the task had already reached Claude, ccc sends
  a short *continue* message instead of repeating the whole task (the
  text is in [Settings](settings.md)).
- Waiting survives restarts: the reset time is stored.

What happens after the reset depends on the conversation's
**auto-continue** switch (on by default; set it when
[starting a conversation](new-conversation.md) or with `o` in the
[task queue](tasks.md)):

| Auto-continue | Status | After the reset |
|---|---|---|
| on | `waiting_retry` `‖` | carries on by itself |
| off | `paused_limit` `‖` | waits for you: press `r` (or send a message) |

## Usage reserve

Want to keep part of your limit for yourself? Set a **usage reserve** in
[Settings](settings.md) (Off, or 50–95 %). With a reserve of 80 %, queued
tasks don't start while the 5-hour or weekly window is at 80 % or more;
they continue after that window resets. A task already running finishes.
Analytics shows the current setting.

## Blocked on an error

A queue stops — `✖` and status `blocked_error` — when a task fails for a
real reason: Claude Code ended with an error, a tool failed, the
conversation's Claude Code session disappeared. ccc never retries such a
task by itself, so it can't loop and burn your limit.

How you find out:

- `✖` in front of the conversation in Conversations and on the Dashboard,
  and the red *blocked* count in the Dashboard overview;
- the line under the conversation: *✖ Blocked: (the error) — press r to
  retry*;
- a red **Retry blocked queue (r)** button on the left in Conversations
  and the queue detail;
- a message on Telegram / Discord, if you set them up
  ([Settings](settings.md)).

What to do: look at the error (Conversations, or the last error in the
queue detail — `Enter` on the Dashboard), fix the cause, then press `r`.
**`r` runs the task that failed again**, before the rest of the queue.

To drop the failed task instead: pause the queue (`t`, `p`), press `r`,
delete the task in the queue (`Delete`) and resume it (`p`).
