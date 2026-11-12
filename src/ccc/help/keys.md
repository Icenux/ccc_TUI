# Keyboard shortcuts

Keys without Ctrl or Alt don't work while you're typing in a text field;
Ctrl and Alt keys work everywhere. The bar at the bottom of the window
lists the keys of the screen you're on — click one to press it.

Every shortcut can be changed in Settings → Keyboard shortcuts (or with
`ccc keys`); ccc checks the new key is free. The other pages of this guide
name the default keys.

## Everywhere

| Key | |
|---|---|
| `d` | Dashboard |
| `c` | Conversations |
| `h` | Archive (archived conversations) |
| `s` | Live sessions |
| `a` | Analytics |
| `n` | New conversation |
| `?` | This help |
| `o` | Routines: prompts on a schedule |
| `A` | Agents: helpers Claude Code can call |
| `t` | Tasks of the selected conversation (Conversations and Archive: their own t) |
| `U` | Update ccc — only when a new version is there |
| `Ctrl+P` | Settings (language, Telegram, queues, presets, shortcuts, backup) |
| `Alt+S` | Screenshot of ccc (an SVG in the export folder, see Settings) |
| `Ctrl+Q` | Close ccc (it keeps running; `ccc` opens it again) |
| `Alt+N` | Open the next conversation that needs you (also from Claude Code) |
| `Ctrl+E` | In a text box: write its text in an external editor (chosen in Settings), it comes back into the box |
| `Esc` | Back |
| `Esc` | Close the window / cancel |

## Dashboard

| Key | |
|---|---|
| `Enter` | Open the queue detail |
| `q` | Close ccc (same as Ctrl+Q) |
| `x` | Archive the selected conversation (a long press on a row offers Archive / Delete) |
| `Del` | Delete the selected conversation from ccc (asks first) |
| `/` | Filter the table: all, running, with pending tasks, blocked (also a click on the filter row) |

## Live sessions

| Key | |
|---|---|
| `Enter` | Send the selected session a prompt |
| `r` | Reload the session list |
| `+` | Add a prompt to a project's queue |

## Conversations

| Key | |
|---|---|
| `Enter` | Open the conversation's Claude Code CLI (in the list) / send the message (in the box) |
| `r` | Blocked: run the failed task again / resume one paused at a limit |
| `t` | Tasks editor (queue, schedule, pause, auto-continue) |
| `p` | Approve a tool call Claude is waiting on |
| `x` | Archive (moves it to Archive, h) |
| `Alt+0` | Switch between ccc and Claude Code (while it's open beside ccc) |
| `Ctrl+]` | Close Claude Code beside ccc (it keeps running; Enter in the list opens it) |
| `/` | Search: Conversations, Archive or everywhere |
| `f` | Filter the list: name or folder; ! working, ? approval, x error, * new (Esc cancels) |
| `F2` | Rename the selected conversation |
| `m` | Groups: put the conversation into one of your own; on a group's heading, rename / delete it |
| `Ctrl+↓` | Jump to the next group's heading (Enter or a click folds / unfolds it) |
| `Ctrl+↑` | Jump to the previous group's heading |
| `e` | Export the conversation as Markdown |
| `v` | Review its changes with a fresh reviewer (once or until it passes) |
| `g` | Goal loop: rounds of work, check and review until the goal is met |
| `w` | Workflow: plan, approve, build, check, parallel reviews, arbiter |
| `Ctrl+J` | New line in the message box (Shift+Enter where supported) |
| `Alt+Enter` | Send the message right away, past the queue (Ctrl+Enter where supported) |
| `Alt+1` | Preset prompt 1–9 (⏎ sends it, ✎ puts it into the message box; set in Settings) |
| `Alt+2` | Preset prompt 2 |
| `Alt+3` | Preset prompt 3 |
| `Alt+4` | Preset prompt 4 |
| `Alt+5` | Preset prompt 5 |
| `Alt+6` | Preset prompt 6 |
| `Alt+7` | Preset prompt 7 |
| `Alt+8` | Preset prompt 8 |
| `Alt+9` | Preset prompt 9 |

## Archive

| Key | |
|---|---|
| `x` | Restore the conversation |
| `Enter` | Restore it and open it in Conversations |
| `Del` | Delete the conversation from ccc (asks first; Claude Code's transcript stays) |
| `/` | Search: Conversations, Archive or everywhere |
| `f` | Filter the list: name or folder; ! working, ? approval, x error, * new (Esc cancels) |
| `F2` | Rename the selected conversation |
| `m` | Groups: put the conversation into one of your own; on a group's heading, rename / delete it |
| `Ctrl+↓` | Jump to the next group's heading (Enter or a click folds / unfolds it) |
| `Ctrl+↑` | Jump to the previous group's heading |
| `e` | Export the conversation as Markdown |

## Tasks editor (t)

| Key | |
|---|---|
| `a` | Add a task |
| `e` | Edit the task (also Enter) |
| `Del` | Remove the task |
| `Ctrl+↑` | Move up |
| `Ctrl+↓` | Move down |
| `p` | Pause / resume the queue |
| `o` | Toggle auto-continue |
| `Ctrl+S` | Save in the task editor |

## Routines (o)

| Key | |
|---|---|
| `a` | Add a routine |
| `e` | Edit the routine (also Enter) |
| `Space` | Turn it on / off |
| `r` | Run it now (its next time stays) |
| `Del` | Delete it (tasks it queued stay) |
| `Ctrl+S` | Save in the routine editor |

## Agents (A)

| Key | |
|---|---|
| `a` | Add an agent from a template (name, where it is kept, preview of the file) |
| `e` | Edit the agent: description, tools, model, prompt (also Enter) |
| `Del` | Delete the agent (its file moves to ccc's own folder) |
| `Ctrl+S` | Create / save the agent in its window |

## Review (v)

| Key | |
|---|---|
| `Ctrl+S` | Start the review |
| `Esc` | Close |

## Goal (g)

| Key | |
|---|---|
| `Ctrl+S` | Start the goal loop |
| `Esc` | Close |

## Workflow (w)

| Key | |
|---|---|
| `Ctrl+S` | Start the workflow |
| `Esc` | Close |

## Approval window (p)

| Key | |
|---|---|
| `y` | Allow |
| `a` | Always allow this tool in this conversation |
| `n` | Deny |

## Confirmation (e.g. deleting)

| Key | |
|---|---|
| `y` | Yes |
| `Enter` | Yes |
| `n` | No |

## Analytics

| Key | |
|---|---|
| `←` | Previous calendar month |
| `→` | Next calendar month |

## Queue detail

| Key | |
|---|---|
| `+` | Add a prompt (this queue's path prefilled) |
| `v` | Review its changes with a fresh reviewer (once or until it passes) |
| `g` | Goal loop: rounds of work, check and review until the goal is met |
| `w` | Workflow: plan, approve, build, check, parallel reviews, arbiter |
| `r` | Blocked: run the failed task again / resume one paused at a limit |

## Preset prompt editor (Settings → Add / Edit)

| Key | |
|---|---|
| `Ctrl+S` | Save |
| `Esc` | Cancel |

## Settings (Ctrl+P)

| Key | |
|---|---|
| `Ctrl+S` | Save |
| `Esc` | Cancel (nothing is saved) |
