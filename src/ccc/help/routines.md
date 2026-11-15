# Routines

A routine is a prompt ccc queues **on a schedule**: a nightly review of
the day's changes, a check every hour, a summary on weekdays at six. The
daemon runs routines, so they run **with ccc closed** too, as long as
the computer is on.

Press `t` anywhere to open **Tasks & Routines**: the routines are the
lower table, with their schedule, next and last run (`Tab` moves to it).
A routine stays until you delete it.

| Key | |
|---|---|
| `+` | add a routine |
| `e` / `Enter` | edit it |
| `Space` | turn it on / off |
| `r` | run it now (its next time stays) |
| `Delete` | delete it (tasks it already queued stay) |
| `Esc` | close |

## Adding one

- **Name**: for the list (empty = the start of the prompt).
- **Conversation**: the conversation it belongs to (the one selected in
  Conversations is prefilled), and where it runs:
  - **In this conversation**: the prompt goes into its
    [task queue](tasks.md), like a task you added. Claude remembers the
    earlier runs, which suits "what changed since yesterday?".
  - **A new conversation every run**: a new headless conversation in the
    same folder, with the same options (model, permissions). Every run
    starts clean; it's archived once it's done. One that got blocked
    stays, so you see what went wrong. Marked `✚` in the list.
- **When**: every N minutes, every N hours, every day at HH:MM, Monday to
  Friday at HH:MM, once a week (`monday 08:00`, also Czech `po 08:00`), or
  a cron pattern (`0 22 * * *`). Under the field you see the schedule in
  words and the next run, or why it won't do.
- **Prompt**: what Claude gets.

`Ctrl+S` saves, `Esc` cancels.

## How runs work

- A routine's task is an ordinary task: [usage limits](limits.md), the
  [commit](git.md) and notifications work as for any other. In the
  [task queue](tasks.md) it's marked `↻`.
- **Missed runs** (the daemon or the computer was off): caught up **once**
  when the daemon is back, then on schedule again.
- **Still running**: while the previous run of a routine hasn't finished,
  a new one is skipped. The list says so and counts the skipped runs.
- A routine whose conversation is [archived](archive.md) doesn't run;
  deleting the conversation deletes its routines.

From the shell: `ccc routine` (see [Command line](command-line.md)).
