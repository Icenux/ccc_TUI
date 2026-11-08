# Agents

An agent (Claude Code calls it a *subagent*) is a **helper with one job**:
"run the tests and report only the result", "map where this function is
used", "review the diff". Claude hands it a task, the helper works in its
own context, and Claude gets back just the summary. That keeps the
conversation short and the usage low.

Press `A` anywhere to open **Agents**: the helpers Claude Code would find
for the selected conversation.

| Key | |
|---|---|
| `↑` / `↓` | choose an agent; below the list you see its file, tools and the start of its prompt |
| `Esc` | close |

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
