# Goal loop

Say once **what done looks like**, and ccc keeps the conversation
working until an independent reviewer agrees. You don't have to check
each attempt and say "try again".

Press `g` in [Conversations](conversations.md) or in the queue detail to
open the **Goal** window of the selected conversation.

## Setting a goal

- **Goal**: specific enough to be checked. "The auth middleware rejects
  expired tokens, with a test that proves it" works; "improve auth" does
  not.
- **Check command** (optional): run after every round in the project
  folder, e.g. `pytest -q` or `npm test && npm run lint`. ccc runs it only
  in conversations started with *Bypass permissions*, where Claude
  may run any command anyway; elsewhere the check is left out.
- **Rounds**: at most this many (5 by default, at most 20).
- **Start** (`Ctrl+S`): the first task, the goal, goes into the
  conversation's [task queue](tasks.md).

## A round

1. Claude works on the task, like any task in the queue.
2. ccc runs the check command. If it fails, the round fails at once and
   its output goes back to Claude; no review is needed to tell a build is
   broken.
3. A fresh [reviewer](review.md) (clean context, can't change anything)
   judges the code against the goal: **PASS** only when it's fully met.
4. PASS ends the loop. Otherwise the reviewer's report becomes the next
   round's task.

## When progress stalls

Sending the same failure back the same way rarely helps, so:

| Round | What changes |
|---|---|
| 1, 2 | nothing; the reason goes back to Claude |
| from 3 | the **reviewer** runs on a stronger model (Opus), in case it's the one getting it wrong |
| from 4 | **Claude** runs on a stronger model too (conversations in the background; an interactive one keeps its model) |
| 5 | Claude is told to stop patching and rethink its approach |

## When it stops

- **met**: the reviewer said PASS;
- **stuck**: the same failure twice in a row (the same verdict reason, or
  the same check output);
- **out of rounds**: the limit was reached;
- **failed**: a task of the loop failed (see the conversation) or was
  removed from the queue;
- **stopped**: you pressed **Stop**. Its waiting task is skipped; one
  already running finishes.

How it ended comes to Telegram / Discord when they're set up. Every
round is a real task plus a real review: both count in your usage.

From the shell: `ccc goal` (see [Command line](command-line.md)). With
`--wait` the exit code is 0 when the goal is met, 1 otherwise.
