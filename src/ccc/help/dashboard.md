# Dashboard

The Dashboard (`d`) is the overview: every conversation with what it's
doing right now. It's only for looking — you work with a conversation in
[Conversations](conversations.md).

![Dashboard](../../../docs/screenshots/dashboard.png)

## The header and the filter

The panel starts with **DASHBOARD** and the number of conversations; on
the right, `[n] new conversation` (a click or `n` opens it).

The row under it filters the table: **all** · **running** (Claude is
working on them) · **pending** (tasks waiting in their queues) ·
**blocked** (stopped on an error). Click one, or press `/` to go round
them; the chosen one is green. Next to them: the tokens used today and,
in yellow, how many conversations **wait for you** (a tool approval, an
error, a usage limit, a finished reply you haven't looked at — the same
ones the Alt+N key jumps to).

## The table

One row per conversation (archived ones are in the [Archive](archive.md));
the selected one is green, with `>` in front:

| Column | |
|---|---|
| Project | the conversation's name, with its marker (see below), and under it the project folder Claude works in (*Path*), shortened from the front — the last folder is always shown |
| Branch | the branch, uncommitted lines (`+12−3`) and commits ahead `↑` / behind `↓` its base branch; `–` outside git, and for a conversation where Claude may not run commands itself (ccc runs git only there, see [Git](git.md)) |
| Queued | tasks waiting in its [queue](tasks.md) |
| Ctx | how full its context is (yellow from 80 %, red from 90 %) |
| Status | what it's doing — see the table below |
| Goal | its latest [goal loop](goal.md): `◎ 3/5` (round / rounds) while it runs, `✅ met 3/5` / `⛔ stuck 4/5` / `⛔ out of rounds 5/5` / `✖ failed` / `■ stopped` for a day after it ended; `–` otherwise |
| Tokens | tokens it used today |

Under the table: how many rows are shown of how many, and the keys.

**The selected conversation** is described under the table: its folder,
Claude Code options and auto-continue; how many tasks are queued, how
full the context is, today's tokens, the branch; on the right its newest
commit (git folders where Claude may run commands) and the keys
`[↵] open` and `[t] tasks`.

**Activity** at the bottom is what happened lately in all conversations,
newest first: `task` (a finished task, with its commit), `queue` (tasks
waiting), `limit` (a usage limit), `approve` (a tool call waits for you),
`error` and `review`.

**Which columns** (*Path* is the folder under the name) — Settings → Dashboard: one choice for the terminal
version of ccc (the Android app has its own). The name is always shown;
by default everything else is too.

**Markers** in front of the name — the same in the Conversations list:

| Marker | Meaning |
|---|---|
| gold `⣾⣷⣯⣟⡿⢿⣻⣽` (turning) | Claude is working |
| `‖` | waiting for a usage-limit reset, or paused |
| red `●` | stopped on an error — needs you |
| blue `●` | Claude is waiting for you to [approve a tool call](approvals.md) |
| green `●` | finished — a new reply you haven't looked at yet |
| `○` | idle, and you have already looked at its last reply |

In the Conversations list `·3` after the name means three tasks queued.

**Status:**

| Status | |
|---|---|
| `IDLE` | nothing to do; the next task starts as soon as you add one |
| `RUNNING` | Claude is working on a task |
| `WAITING` | waiting for a usage limit to reset; then it carries on by itself |
| `LIMIT` | stopped at a usage limit with auto-continue off — waits for you |
| `BLOCKED` | stopped on an error — waits for you |

What to do with the last two: [Usage limits & blocked queues](limits.md).

## Queue detail

`Enter` on a row opens the conversation's detail:

- its settings (folder, Claude Code options, auto-continue) and, for a
  [remote-control](remote-control.md) conversation, the link to open it
  on your phone;
- **Now**: whether Claude works, waits for your approval or for a usage
  limit, how many messages are queued and the last thing that happened
  in the conversation;
- **Changes**: the folder's branch and the files with uncommitted
  changes, with the lines added and removed (only in a git folder and in
  conversations started with *Bypass permissions*, as ccc runs
  git only there);
- **Usage**: the tokens of the last 14 days and the models used;
- **Errors**: the latest runs that failed, and what blocks the
  conversation now. The page refreshes by itself;
- `+` adds a task to this conversation, `r` gets it going again when it's
  blocked or paused at a limit. `Esc` goes back.

![Queue detail](../../../docs/screenshots/queue_detail.png)

## An empty Dashboard

Before your first conversation the Dashboard shows **Getting started**
instead — three steps to begin. Press `n`.
