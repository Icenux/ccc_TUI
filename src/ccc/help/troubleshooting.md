# Troubleshooting & how it works

## The sidebar says "daemon not running"

Nothing gets done without the daemon. Check and start it:

```bash
systemctl --user status ccc        # is it running?
systemctl --user restart ccc
journalctl --user -u ccc -f        # its log
```

To keep it running after you log out or close SSH, enable lingering once:
`loginctl enable-linger "$USER"`.

**Encrypted home (ecryptfs):** the services can't start at boot, before
you log in, because their files are still encrypted. Start them at login
instead, e.g. with an autostart entry of your desktop that runs
`systemctl --user daemon-reload && systemctl --user start ccc.service ccc-tui.service`.

## `ccc` doesn't open / shows something odd

The always-on ccc runs in its own tmux server (socket
`/tmp/ccc-tmux-<uid>/ccc`), so it doesn't show up in plain `tmux ls`.
`ccc` attaches to it; if the `ccc-tui` service isn't running, `ccc`
starts it. If ccc itself quits, it is back a second later.
`systemctl --user restart ccc-tui` restarts it.

## ccc and your own tmux

ccc's tmux servers are kept apart from yours: their sockets are in
`/tmp/ccc-tmux-<uid>/` (not next to yours in `/tmp/tmux-<uid>/`) and they
never load your `~/.tmux.conf`, so changing your tmux setup doesn't
change ccc. Two environment variables move them further:

- `CCC_TMUX_BIN` — the tmux program ccc uses (e.g. a build of its own,
  so upgrading the system tmux doesn't touch ccc);
- `CCC_TMUX_DIR` — the directory for ccc's sockets.

Set them for the services (e.g. in `~/.config/environment.d/ccc.conf`,
then log in again or `systemctl --user daemon-reload` and restart
`ccc ccc-tui`) **and** in the shell you run `ccc` from — both must use
the same tmux.

Running `ccc` inside your own tmux nests it: your tmux sees the keys and
the mouse first (its prefix, mouse mode, copy mode, clipboard settings).

## A conversation stopped

`✖` = stopped on an error, `‖` = waiting for a usage limit or paused.
See [Usage limits & blocked queues](limits.md).

## Restoring a backup

1. `systemctl --user stop ccc ccc-tui`
2. Copy a backup from `~/.local/share/ccc/backups/` over
   `~/.local/share/ccc/history.db`.
3. `systemctl --user start ccc ccc-tui`

## Claude Code in ccc's own frame (experimental)

By default Claude Code opens in a tmux pane next to ccc, so the lines
around it are tmux's (straight corners, a 2-column offset in the title).
With `CCC_EMBEDDED_TERMINAL=1` in the environment of `ccc-tui` ccc shows
Claude Code itself: a rounded frame like the menu's, with the title,
the conversations waiting for you, **⌨ typing here** and the preset
prompts under it. tmux stays only as the window ccc lives in (it keeps
running and you can attach from anywhere) and as the host of the
conversations' sessions, so nothing changes for the daemon, the API or
the phone app. The keys are the same: Alt+0 moves the keyboard between
the list and Claude Code, Ctrl+] closes the frame (Claude keeps running).
Drag with the mouse to select text — it goes to the clipboard when you
let go; the wheel scrolls Claude's history.

Turn it on: `systemctl --user edit ccc-tui`, add
`[Service]` / `Environment=CCC_EMBEDDED_TERMINAL=1`, then
`systemctl --user restart ccc-tui`. Remove the line to go back.

## How ccc works

- **`ccc-daemon`** is the only thing that ever runs Claude. It's a
  background service that survives SSH logout, crashes (it's restarted)
  and reboots.
- **The TUI** only reads and writes ccc's database; it never runs a
  prompt. Close it any time.
- Both share one SQLite database (`~/.local/share/ccc/history.db`); a task
  is claimed atomically, so it never runs twice.
- Conversations with Claude Code beside ccc run as interactive `claude`
  processes in a hidden tmux server (socket `/tmp/ccc-tmux-<uid>/ccc-sessions`), kept alive
  by the daemon.
- ccc uses your normal Claude Code login — no API key — and the **same**
  5-hour / weekly usage as any other Claude Code session on the machine.
  Costs shown are Anthropic's list-price equivalent, not an extra charge.
- A usage limit is recognised from Claude Code's own error status (429),
  not by guessing from text. When the reset time is unknown, ccc retries
  after 15, then 30, then 60 minutes.
- The language (Settings → Appearance, or `ccc config language`) applies
  to the TUI, this guide, notifications, the monthly archive notes and
  `ccc`'s output.
