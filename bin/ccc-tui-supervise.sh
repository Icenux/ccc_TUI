#!/usr/bin/env bash
# Runs as ExecStart of the ccc-tui systemd unit: creates the
# detached "ccc" tmux session (on ccc's own tmux server, see
# src/ccc/tmux.py) with the TUI in a restart loop, then blocks for as long
# as that session exists. Exits non-zero as soon as the session is gone, so
# systemd's Restart=always recreates it.
#
#   ccc-tui-supervise.sh        start and wait (ExecStart)
#   ccc-tui-supervise.sh stop   end the session (ExecStop)
#
# Not meant to be run by hand — `ccc` attaches to it.
#
# ccc's tmux is kept apart from the user's (#280), the same way as
# src/ccc/tmux.py does it — keep the two in step:
# - binary: CCC_TMUX_BIN (default `tmux` on PATH);
# - socket: in CCC_TMUX_DIR (default /tmp/ccc-tmux-<uid>, 0700), not in
#   tmux's /tmp/tmux-<uid> next to the user's servers;
# - -f /dev/null when the server starts: no user tmux.conf, so no plugins.
#   tmux-continuum/resurrect would otherwise restore the user's sessions
#   (even re-launching `claude --continue` in them) into this server.
set -euo pipefail

TMUX_SOCKET="ccc"
SESSION="ccc"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# A system package (the .deb) points CCC_BIN at /usr/bin/ccc; a checkout uses its own virtualenv.
CCC_BIN="${CCC_BIN:-$PROJECT_DIR/.venv/bin/ccc}"
RUN_CMD="while true; do $CCC_BIN tui; sleep 1; done"
TMUX_BIN="${CCC_TMUX_BIN:-tmux}"
SOCKET_DIR="${CCC_TMUX_DIR:-/tmp/ccc-tmux-$(id -u)}"

mkdir -p -m 700 "$SOCKET_DIR"
if [ -L "$SOCKET_DIR" ] || [ ! -O "$SOCKET_DIR" ]; then
    echo "$SOCKET_DIR is not a directory of $(id -un), refusing to use it" >&2
    exit 1
fi
chmod 700 "$SOCKET_DIR"

own() { "$TMUX_BIN" -S "$SOCKET_DIR/$TMUX_SOCKET" -f /dev/null "$@"; }

if [ "${1:-}" = "stop" ]; then
    own kill-session -t "=$SESSION" 2>/dev/null || true
    exit 0
fi

# A ccc from before #280 runs its TUI on `tmux -L ccc` (in tmux's own
# directory): end it, the TUI comes back here a second later.
if ! own has-session -t "=$SESSION" 2>/dev/null; then
    tmux -L "$TMUX_SOCKET" -f /dev/null kill-session -t "=$SESSION" 2>/dev/null || true
    own new-session -d -s "$SESSION" -x 200 -y 50 -c "${CCC_START_DIR:-$PROJECT_DIR}" "$RUN_CMD"
fi

while own has-session -t "=$SESSION" 2>/dev/null; do
    sleep 5
done

echo "tmux session $SESSION is gone, exiting so systemd restarts it" >&2
exit 1
