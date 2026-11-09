# Command line

Everything ccc does can be reached from the TUI; a few things also work
from the shell — e.g. in scripts or over SSH. The commands live in the
checkout's virtualenv (`~/ccc/.venv/bin/`); add it to your `PATH` or call
them with the full path. Their output is in ccc's language. Shortcuts changed with `ccc keys`
reach the running ccc when it restarts (`systemctl --user restart
ccc-tui`); changed in Settings they work right away.

| Command | |
|---|---|
| `ccc` | open ccc |
| `ccc add <folder> "<prompt>"` | queue a prompt for that folder's conversation (it runs headless) |
| `ccc backup` | back up the database now |
| `ccc statusline [--then COMMAND]` | Claude Code's [status line](#status-line): tells ccc how full a conversation's context is |
| `ccc archive-analytics` | write finished months into the [monthly analytics archive](analytics.md) now |
| `ccc config analytics-folder [PATH\|off]` | show / set / turn off the monthly archive folder |
| `ccc config continue-text ["…"]` | show / set the continue message sent after a usage limit |
| `ccc config usage-reserve [N\|off]` | show / set the [usage reserve](limits.md) |
| `ccc config language [auto\|en\|cs]` | show / set ccc's language (auto = the system's) |
| `ccc config telegram-token [TOKEN]` | show (masked) / set the Telegram bot token |
| `ccc config telegram-chat [ID]` | show / set the Telegram chat id |
| `ccc config telegram-test` | send a test message to Telegram |
| `ccc config discord-webhook [URL\|off]` | show (masked) / set / remove the Discord webhook |
| `ccc config discord-test` | send a test message to Discord |
| `ccc keys list` | every [keyboard shortcut](settings.md#keyboard-shortcuts): action id, key (`*` = changed), what it does |
| `ccc keys check KEY [ACTION]` | is the key free? (who has it, or why it can't be used); with an action id: free for that action — exit code 1 when it isn't |
| `ccc keys set ACTION KEY` | give the action a new key — refused (exit code 1) when it collides |
| `ccc keys reset [ACTION]` | back to the default key (one action, or all) |
| `ccc keys export FOLDER` | write the shortcuts to `FOLDER/ccc-keys.json` |
| `ccc keys import PATH` | use the shortcuts from a `ccc-keys.json` (or the folder with it) — nothing is imported when they collide |
| `ccc routine list` | every [routine](routines.md): id, on / off, conversation, schedule, next run |
| `ccc routine add CONVERSATION "PROMPT" --daily 18:00` | add a routine to a conversation (id or name); the schedule is one of `--every 30m` / `2h`, `--hourly`, `--daily [HH:MM]`, `--weekdays [HH:MM]`, `--on "monday 08:00"`, `--cron "0 22 * * *"`; `--fresh` = a new conversation every run, `--name` = its name |
| `ccc routine run ROUTINE` | run it now (id or name) |
| `ccc routine on ROUTINE` / `off ROUTINE` | turn it on / off |
| `ccc routine remove ROUTINE` | delete it |
| `ccc review start CONVERSATION ["what to check"]` | ask for a [review](review.md) (the daemon runs it when the conversation is idle); `--until-pass [--rounds N]` = send the findings back and review again; `--wait` = wait for the verdict, exit code 0 PASS (or nothing new), 3 FAIL, 4 PARTIAL, 1 error |
| `ccc review list [CONVERSATION]` | reviews, newest first: id, time, verdict, what was reviewed |
| `ccc review show REVIEW` | a review's report |
| `ccc review stop CONVERSATION` | stop its review (loop) |
| `ccc goal start CONVERSATION "GOAL"` | start a [goal loop](goal.md); `--check "pytest -q"` = run after each round, `--rounds N` = at most N rounds, `--wait` = wait until it ends, exit code 0 when the goal is met |
| `ccc goal list [CONVERSATION]` | goals, newest first: id, start, round, state |
| `ccc goal stop CONVERSATION` | stop its goal loop |
| `ccc workflow templates` | the [workflow](workflows.md) templates and their steps |
| `ccc workflow start CONVERSATION TEMPLATE "TASK"` | run a workflow; `--check "pytest -q"`, `--rounds N`, `--wait` = wait until it ends (exit code 0 when it passed) |
| `ccc workflow list [CONVERSATION]` / `show RUN` | runs, newest first / a run's steps and what each agent said |
| `ccc workflow approve CONVERSATION` / `reject CONVERSATION` | answer the step a run waits at (`--note "…"` for the next step) |
| `ccc workflow stop CONVERSATION` | stop its workflow |
| `ccc workflow import FILE` / `export TEMPLATE FOLDER` / `delete TEMPLATE` | your own templates as JSON |
| `ccc api serve [--port N]` | run the [API for the Android app](android-api.md) (normally run by `ccc-api.service`) |
| `ccc api token [--new]` | make the token the app signs in with (shown only then); `--new` = a new one, every device is signed out |
| `ccc api pair [--server URL] [--image [FILE]]` | show a QR code the app scans to sign in (good for 5 minutes, once); `--image` saves it as a picture instead, for when the terminal's drawing won't scan |
| `ccc api devices` / `ccc api revoke ID` | list the devices signed in (`--all`: also the signed-out ones, history) / sign one out |
| `ccc-daemon` | the daemon itself (normally run by `ccc.service`) |

## Status line

`ccc statusline` makes Claude Code tell ccc how full the context window of an
interactive conversation is — the real figure, not an estimate. The
[app](android-api.md) shows it on the conversation. Put it in
`~/.claude/settings.json`:

```json
"statusLine": {"type": "command", "command": "ccc statusline"}
```

Already have a status line of your own? Hand it over, it gets the same input
and what it prints is what Claude Code shows:

```json
"statusLine": {"type": "command", "command": "ccc statusline --then '~/.claude/my-line.sh'"}
```

Only interactive Claude Code runs the status line (not `claude -p`), and the
figure is as fresh as Claude Code refreshes it.
