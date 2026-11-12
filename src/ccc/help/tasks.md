# Task queue

Every conversation has a **queue of tasks** — messages ccc sends to
Claude one after another, in the same conversation. The next one goes as
soon as Claude has finished the previous one, whether you're watching or
not: you can queue an evening's work and close ccc.

Press `t` in [Conversations](conversations.md) to open the queue of the
selected conversation.

![Task queue](../../../docs/screenshots/tasks.png)

## Keys

| Key | |
|---|---|
| `a` | add a task |
| `e` / `Enter` | edit the selected task |
| `Delete` | remove it (it stays in the history as skipped) |
| `Ctrl+↑` / `Ctrl+↓` | move it up / down |
| `p` | pause / resume the whole queue (a task already running finishes) |
| `o` | turn [auto-continue](limits.md) on / off |
| `Esc` | close |

The top line shows whether auto-continue is on and whether the queue
runs or is paused.

## Adding a task

`a` opens an editor: write the task (several lines are fine), set when it
may start and press `Ctrl+S` to save (`Esc` cancels). `@` offers the
conversation's files, like in the message box — see
[Mentioning files](conversations.md#mentioning-files).

**Run at:**

| You type | It starts |
|---|---|
| *(nothing)* | as soon as possible |
| `23:00` | today at 23:00 — tomorrow if that time has passed |
| `2026-11-02 08:30` | at that date and time |
| `reset` | right after your 5-hour usage limit resets next |

A task scheduled for later **keeps its place** in the queue, so the tasks
behind it wait too. The task list shows a *When* column, and the line
under the conversation says when the next task starts (*Next task at
23:00*).

## How a task is sent

- **Claude Code beside ccc** (conversations started with `n`): ccc waits
  until Claude is idle and pastes the task in, as if you typed it. The
  reply is right there in Claude Code. Idle means Claude has finished its
  turn — something it left running in the background (a shell, a monitor)
  doesn't hold the task back.
- **Headless conversations:** each task is one `claude -p` run; the reply
  appears in ccc's transcript. In a git repository the result can be committed — see
  [Git](git.md).

The message box under a headless conversation adds to the end of the same
queue.

## When something goes wrong

If a task fails, the queue stops (`✖`) and waits for you — it never
retries on its own and burns your limit. `r` runs the failed task again.
See [Usage limits & blocked queues](limits.md).
