# Git: auto-commit

In a git repository, ccc commits the result of every successful
**headless** task, so each task is one commit you can look at or undo:

- message `ccc: <first line of the task>` with a
  `ccc-conversation: <name>` trailer;
- `ccc runs CONVERSATION` lists the commit of each run (see
  [Command line](command-line.md)).

## Undoing a run

`ccc revert RUN` reverts a run's commit with a **new commit**
(`git revert`) — nothing is rewritten, and the revert can be undone in
turn. The run then shows `↺` by its commit in `ccc runs`. `--tell` also
queues a short note for Claude in the conversation; otherwise its next
task may still assume the changes are there.

It works when:

- the conversation does nothing at the moment (no task running or
  queued) — a task editing the files would mix its edits into the
  revert;
- the folder has no uncommitted changes, and the commit is in the
  branch's history;
- the conversation was started with **Skip all permission checks** (as
  for the commits themselves, see below).

A revert that conflicts with later changes is abandoned: nothing
changes and ccc says so. `ccc runs CONVERSATION` lists the runs with
their ids.

## Several conversations in one folder

Two conversations editing the same files at once get in each other's
way, and a commit after one task could take in the other's half-written
changes. So in one git work tree:

- **headless tasks take turns**: a task waits while another
  conversation in the same folder runs one (Conversations says which);
- ccc **doesn't commit** while another conversation works in the folder
  (also an interactive one busy with what you typed) — `ccc runs`
  says so;
- Conversations shows a ⚠ line with the others that share the folder.

To let them really work **side by side**, give a conversation its **own
git worktree** when [starting it](new-conversation.md): a separate
checkout of the repository on its own branch (`worktree-<name>`, in
`.claude/worktrees/<name>` — where `claude --worktree <name>` keeps
its own, left out of the main checkout's `git status`). ccc commits its
tasks there; merge the branch when you're happy with it. Remove a
worktree you no longer need with `git worktree remove .claude/worktrees/<name>`.

Conversations shows where each one stands in git — in the list after
its name and in the header: `+12−3` lines changed and not committed yet,
`↑2` commits ahead of and `↓1` behind its base (the branch's upstream,
or for a worktree the branch of the main checkout it was made from).
Nothing is shown when it's all committed and in step. Like everything
else git in ccc, only for conversations that may run commands; it's
refreshed every 15 seconds, and new files git doesn't track yet aren't
counted.

A fresh checkout has only the tracked files, so `.env`, local
certificates or config the project needs to run would be missing. List
them in a **`.worktreeinclude`** file at the repository's top (gitignore
syntax — the same file Claude Code, Conductor and Codex read), e.g.:

```
.env
config/local.json
```

ccc copies into each new worktree the files that match a pattern **and**
are ignored by git — never a tracked file, never through a symbolic link,
never over an existing file, at most 500 files of up to 20 MB each.

## When ccc doesn't commit

`ccc runs` says why:

- the folder already had **uncommitted changes** before the task — that's
  your work, ccc never sweeps it into its commit;
- nothing changed, or the repository is on a detached HEAD;
- the conversation wasn't started with **Skip all permission checks**.
  ccc runs git itself, outside Claude's permission mode, so in an
  *Auto-accept edits* / *Plan only* conversation Claude could otherwise
  slip a git hook in that ccc would run;
- the repository's config uses filters, includes or signing (or changed
  during the task) — for safety ccc then runs no git there.

Conversations with Claude Code beside ccc aren't committed by ccc — Claude
commits there itself when you ask it to.
