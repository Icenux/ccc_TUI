# Approving tool calls

Claude Code asks before it uses a tool — runs a command, edits a file —
unless you allowed it up front. Where you answer depends on the
conversation:

- **Claude Code beside ccc:** Claude asks right there in its pane, as in
  any terminal. Answer it there. (Remote-control conversations can also be
  answered from your phone.)
- **Headless conversations** started with *Accept edits*, *Manual* or
  *Plan*: there's no terminal to ask in, so ccc asks for it. Claude's
  request waits — with no time limit — until you decide.
- *Bypass permissions*: nothing is asked, Claude does everything.

## Answering in ccc

A conversation waiting for you shows `?` in the lists and *Waiting for
your approval: Bash — press p*. Press `p` in
[Conversations](conversations.md):

![Approving a tool call](../../../docs/screenshots/approval.png)

| Key | |
|---|---|
| `y` | allow it this once |
| `a` | always allow this tool in this conversation |
| `n` | deny — Claude is told and works around it |
| `Esc` | decide later |

If you set up Telegram or Discord ([Settings](settings.md)), a message
tells you when a request is waiting.
