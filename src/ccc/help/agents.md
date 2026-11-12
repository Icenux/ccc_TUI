# Agents

An agent (Claude Code calls it a *subagent*) is a **helper with one job**:
"run the tests and report only the result", "map where this function is
used", "review the diff". Claude hands it a task, the helper works in its
own context, and Claude gets back just the summary. That keeps the
conversation short and the usage low.

Press `A` anywhere (or **Agents** in the menu on the left, under Live
sessions) to open **Agents**: the helpers Claude Code would find for the
selected conversation.

| Key | |
|---|---|
| `↑` / `↓` | choose an agent; below the list you see its file, tools and the start of its prompt |
| `a` | add an agent (describe it yourself, or from a template) |
| `e` / `Enter` | edit the selected agent |
| `Delete` | delete the selected agent |
| `Esc` | close |

## Adding an agent

`a` opens **New agent**. Give the agent a name and say where it is kept:
this project or all projects; `Ctrl+S` creates it and the new agent is
selected in the list. You start from one of two things:

- **Describe it myself** (the default): say in one sentence what the agent
  does and *when* Claude should use it, whether it may only read, also run
  commands or also change files, and how thorough it should be (a cheaper
  model for simple jobs). ccc turns that into the file's fields. The
  instructions start as a short outline; **Write the instructions with
  Claude** lets Claude write them instead (a one-off call that can't read,
  write or run anything; it counts against your usage like a short
  question). Read the result and change what you want: nothing is saved
  until you press Create.
- **A template**; under the fields you see the file exactly as it will be
  written:

- **Code reviewer**, **Test runner**, **Code explorer**, **Translator**: each
  has one job, a description that says when to use it, only the tools that
  job needs and, where it is enough, a cheaper model.
- A file that already exists is never overwritten.
- The file is yours afterwards: open it in any editor and change what you
  want. Claude Code picks the change up within seconds.
- Claude Code watches only agent folders that existed when it started. The
  first agent in a new folder needs a restart of Claude Code where it
  already runs (ccc tells you).
- To make a whole conversation run as an agent, see
  [Starting a conversation](new-conversation.md#agent).

## Editing and deleting

`e` (or `Enter`) edits the selected agent: description, tools, model and
instructions. `Ctrl+E` opens the instructions in your external editor.
Saving changes **only those four things**: the name and every other field of
the file (hooks, memory, MCP servers, your comments) stay exactly as they
were. If someone else changed the file while the window was open, ccc
refuses to overwrite it; a file that is a symbolic link is left alone too
(change its target in an editor).

`Delete` removes the agent after a question. The file is **not deleted for
good**: it moves to `~/.local/share/ccc/deleted-agents/` with the date in its
name, so you can put it back by moving it into the agents folder.

## Where agents live

Each agent is a Markdown file:

- **this project**: `.claude/agents/<name>.md` in the conversation's
  folder (or a parent folder up to the repository root). Commit it and
  the whole team has the helper.
- **all projects**: `~/.claude/agents/<name>.md`.

If both define the same name, the project's agent wins; the other one is
shown dimmed ("not used").

## A file Claude Code skips

Claude Code skips a broken agent file **without telling you**. Agents
shows it in red with the reason, for example *no description* (the
description is how Claude decides when to call the agent) or a name that
starts with `-` or contains `:`.

## A good agent

- **One job**, said in the description, with *when* to use it ("Use after
  code changes"). A vague description means Claude never calls the agent.
- **As few tools as it needs**: a reviewer or an explorer only reads
  (`Read, Grep, Glob`).
- **A cheaper model** (`haiku`, `sonnet`) when the job allows.

Claude calls agents by itself. To make sure one runs, name it in your
message.
