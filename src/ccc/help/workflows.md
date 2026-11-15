# Workflows

A **workflow** sends a conversation's task through a chain of
specialists: a planner, your approval, the builder (the conversation
itself), a check, several independent reviewers at once and an arbiter
who weighs their findings. When the work doesn't pass, it goes back to
the builder with what was found — a bounded number of times.

Press `w` in [Conversations](conversations.md) or in the queue detail to
open the **Workflow** window of the selected conversation.

## Starting one

1. Pick a **template**. Under it you see its steps, e.g.
   `plan → your approval → build → check → review ×2 → arbiter`.
2. Write the **task**.
3. Optionally the **check command** for its check step (`pytest -q`) and
   how many **rounds** back to the build it may take.
4. **Start** (`Ctrl+S`). The daemon runs the steps; the window shows each
   one as it goes (✓ done, ▶ now, ⏸ waits for you, ○ to come) and what
   every agent said.

## The steps

| Step | What happens |
|---|---|
| plan | a planner reads the code and writes a plan with the **acceptance criteria** the work will be judged by |
| your approval | the run waits: read the plan, **Approve** (with an optional note for the next step) or **Reject** (the run stops). You're notified on Telegram / Discord |
| build | the task (with the plan, and in later rounds the findings) goes into the conversation's [task queue](tasks.md) |
| check | ccc runs the check command; a failure goes straight back to the build. Only in conversations with *Bypass permissions*, as for the [goal loop](goal.md) |
| review | 1–4 reviewers **in parallel**, each with its own brief (e.g. correctness, security) and model |
| arbiter | compares the reviewers' reports, checks what they disagree on in the code itself and gives the verdict |

A review without an arbiter after it passes only when every reviewer
says PASS. Every agent except the builder **only reads** — like the
[reviewer](review.md): ccc gives them the diff, they can't change or run
anything and don't load the project's settings. Their tokens count in
[Analytics](analytics.md).

The run ends **passed** after the last step, **failed** when it's out
of rounds or you reject it, **error** when a task or an agent fails, or
**stopped** by you. One workflow, goal loop or review at a time per
conversation.

## Templates

Built in:

- **Plan → approve → build → review**
- **Build → two reviews in parallel → arbiter** (correctness + security)
- **Plan → approve → build → two reviews → arbiter**

Your own are JSON files, imported with `ccc workflow import FILE`
(export one to start from with `ccc workflow export NAME FOLDER`). The
import first shows everything the template tells the agents and any
command it runs, and saves it only with `--yes` — a template from
someone else steers Claude in your project, so read it. A check command
you give when starting a run wins over the template's.

```json
{
  "name": "careful",
  "title": "Plan, build, three reviews",
  "max_rounds": 3,
  "steps": [
    {"type": "plan", "model": "opus"},
    {"type": "gate", "text": "Approve the plan?"},
    {"type": "code", "brief": "Keep the public API unchanged."},
    {"type": "check", "command": "pytest -q"},
    {"type": "review", "reviewers": [
      {"name": "correctness", "brief": "Does it do what the task asks? Tests?"},
      {"name": "security", "brief": "Input validation, secrets, permissions.", "model": "opus"},
      {"name": "performance", "brief": "Needless queries or loops on hot paths."}
    ]},
    {"type": "arbiter", "model": "opus"}
  ]
}
```

`type` is one of `plan`, `gate`, `code`, `check`, `review`, `arbiter`;
`brief`, `model` and `text` are optional. An arbiter comes right after a
review; check, review and arbiter need a build step before them.
`max_rounds` is 1–10.

From the shell: `ccc workflow` (see [Command line](command-line.md)).
