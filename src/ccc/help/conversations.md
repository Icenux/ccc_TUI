# Conversations

Conversations (`c`) is where you work with Claude. On the left is the list
of your conversations, on the right the selected one. Move in the list
with `↑` `↓` — the right side follows at once.

![Conversations](../../../docs/screenshots/conversations.png)

## The list

Each conversation shows a marker in front of its name and `·N` behind it
when N tasks wait in its [queue](tasks.md):

| Marker | Meaning |
|---|---|
| `●` | Claude is working |
| `‖` | waiting for a usage-limit reset, or paused |
| `✖` | stopped on an error — needs you |
| `?` | Claude is waiting for you to [approve a tool call](approvals.md) |
| `◆` | a new reply you haven't looked at yet |
| `○` | idle, and you have already looked at its last reply |

With no conversation at all, the right side says **NO CONVERSATION** —
press `n` to start one.

## Groups

The list is split into **groups** under foldable headings —
`▾ shop (3) · ?1 ●2`: the name, how many conversations it holds and what
they're doing (the markers above, counted). A conversation goes under its
**project** — the git repository it works in (a [worktree](new-conversation.md)
under its repository), or its folder outside git; two projects with the
same name show their parent folder too (`work/app`, `home/app`).

| Key | |
|---|---|
| `Enter` or a click on a heading | fold / unfold the group — a folded one still counts what its conversations are doing |
| `Ctrl+↓` / `Ctrl+↑` | jump to the next / previous heading (around the end) |
| `m` on a conversation | put it into a **group of your own** — pick one or type a new name; *none of its own* takes it back to its project |
| `m` on your own group's heading | rename it (to an existing group's name: they merge) or delete it — no conversation is deleted, they go back to their projects |

Your own groups come first, then the projects, both by name. Folded
headings stay folded (also after restarting ccc); opening a conversation
that is in a folded group (`Alt+N`, search) unfolds it. The filter (`f`)
shows every group unfolded. In Settings → Queues, *Groups in the list*
can be set to *Only your own groups*: the other conversations are then
under *No group*, and without any group of your own the list is flat.
The [Archive](archive.md) is grouped the same way. The keys can be
changed in Settings → Keyboard shortcuts.

## Filtering the list

With dozens of conversations, `f` opens a filter above the list. Type
and the list narrows to conversations whose **name or folder** contains
what you typed — case and diacritics don't matter (`prilis` finds
*Příliš*); several words must all match.

Markers at the start filter by **state** — each a word of its own,
combined with text as you like (`? api`, `!? api`, `x`):

| Marker | Shows |
|---|---|
| `!` | Claude is working |
| `?` | waiting for your approval |
| `x` | stopped on an error |
| `*` | a new reply you haven't seen |

Several markers show any of those states. `Enter` opens the first result,
`Esc` cancels the filter. While it's on, the list's title says so
(`Queues · filter: api`); when nothing fits, the list says *Nothing
matches*. The filter stays while the list refreshes, until you cancel it
or leave the view; the conversation you opened from it stays listed even
when it stops matching (a `*` one is no longer new once you look at it).
The key can be changed in Settings → Keyboard shortcuts. It works the
same in the [Archive](archive.md).

## Claude Code beside ccc

A conversation started with `n` runs the **real Claude Code** — the same
as typing `claude` in a terminal, with every feature: slash commands,
tool approvals, `Shift+Tab`, `Esc Esc`, history, images. ccc shows it to
the right of its menu:

| Key | |
|---|---|
| `Enter` on a conversation | open its Claude Code and put the keyboard there |
| `Alt+0` | switch the keyboard between ccc and Claude (or click where you want to type) |
| `Ctrl+]` | close Claude's pane — Claude keeps running, `Enter` opens it again |
| `Alt+N` | open the next conversation that needs you, here in the pane (also from Claude Code) |
| select with the mouse | copies to your clipboard (also over SSH and in Termux); the selection stays shown until you type or click |
| `Ctrl+Shift+C` / `Ctrl+Shift+V` | copy the selection / paste the last copy |

Under Claude a bar shows your [preset prompts](settings.md) — click one
to use it. `Alt+0` and `Ctrl+]` are also in ccc's bar at the bottom.

**What needs you** shows in Claude's title bar, so you see it while you
type there — or when Claude takes the whole window on a phone, where the
list isn't visible: `? shop-api waits for approval · ✖ infra error ·
◆ docs-site done`. The same states as the list's markers — a tool
approval waiting, an error, paused at a usage limit, a finished run you
haven't seen — most urgent first, without the conversation you're in;
what doesn't fit ends as `+N more`. It's gone when nothing waits.
`Alt+N` opens the first one.

Good to know:

- Claude keeps running when you switch to another conversation or screen,
  close ccc (`Ctrl+Q`) or close the pane. The daemon keeps it alive in the
  background and starts it again with its history if it dies.
- Tasks from the [queue](tasks.md) are pasted into Claude whenever it's
  idle, as if you typed them.
- On a narrow window (under 100 columns, e.g. a phone) Claude takes the
  whole window; `Alt+0` gets you back to ccc.
- Every such conversation keeps a Claude Code process in memory —
  [archive](archive.md) (`x`) the ones you're done with.

## Headless conversations

Conversations that share a folder show a ⚠ line: their headless tasks
take turns, see [Git](git.md#several-conversations-in-one-folder).

Prompts queued with `+` (Live sessions, queue detail) or `ccc add` run
**headless**: the daemon runs `claude -p` for each task, without a
terminal. For these ccc shows the transcript itself:

- Claude's replies are rendered as Markdown; a tool call is a collapsed
  `▸ ⚙ Tool: …` line — `Enter` or a click shows its input and result.
- The **message box** under the transcript adds a message to the end of
  the queue: `Enter` sends, `Ctrl+J` starts a new line, `Ctrl+E` opens
  the text in your [external editor](settings.md#external-editor).
  `Alt+1` … `Alt+9` send or insert a [preset prompt](settings.md).
- `@` in the box mentions a file — see *Mentioning files* below.
- The line above the box says what's going on: *Ready*, *Next task at
  23:00*, *Waiting for usage limit reset until 14:30*,
  *✖ Blocked: … — press r to retry*, …
- A headless run can't stop and ask you to approve a tool, so its
  permission mode is chosen up front — see
  [Approving tool calls](approvals.md).

## The header

At the top: the conversation's name, project folder and Claude Code
options, whether auto-continue is on, and for a
[remote-control](remote-control.md) conversation the link for your phone.

## Mentioning files

To point Claude at a file, type `@` in the message box (or in a
[task](tasks.md)): a list of the conversation's files opens above the
box. Typing narrows it — the text in the file name first, then anywhere
in the path, then its letters in order (`@lgn` finds `login.py`).
`↑` `↓` pick, `Tab` or `Enter` inserts the path as Claude Code knows it,
relative to the conversation's folder: `@src/app/login.py`. `Esc` closes
the list and the `@` stays a plain character; so does an `@` inside a
word (`me@example.com`).

The files come from `git ls-files` (tracked and new, not ignored) where
Claude may run commands anyway (*Skip all permission checks*); elsewhere
ccc walks the folder without running anything — up to 20,000 files,
skipping `.git`, `node_modules`, `.venv`, `build` and the like. The list
is read in the background and kept for 30 seconds, so a big repository
doesn't slow typing down.

## Other keys

| Key | |
|---|---|
| `t` | the [task queue](tasks.md) of this conversation |
| `r` | stopped on an error: run the failed task again; paused at a limit: carry on — see [Usage limits](limits.md) |
| `p` | [approve a tool call](approvals.md) Claude is waiting on |
| `x` | [archive](archive.md) the conversation |
| `/` | search all conversations (archived too); `Enter` on a hit opens it |
| `f` | filter the list by name, folder and state (see *Filtering the list*) |
| `m` | put the conversation into a group / rename or delete the group (see *Groups*) |
| `e` | export the conversation as Markdown (to `conversations/` in the export folder, see [Settings](settings.md)) |
| `n` | [start a new conversation](new-conversation.md) |
