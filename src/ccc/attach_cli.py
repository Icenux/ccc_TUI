"""`python -m ccc.attach_cli QUEUE_ID` — what the Claude Code pane next to
ccc runs (tui.cli_pane): wait until the daemon has started the
conversation's interactive `claude`, then show it — a nested tmux client
attached to its session on the ccc-sessions server, so it's the real CLI
with every feature. Ctrl+] (or the session ending) closes the pane;
Claude keeps running. `python -m ccc.attach_cli QUEUE_ID [DETACH_KEY]`:
the tmux name of the key that closes it, when it isn't Ctrl+]."""
from __future__ import annotations

import os
import sys
import time

from ccc import managed_sessions
from ccc.i18n import _

WAIT_POLL_SECONDS = 0.5
# After this long without the session, say the daemon may not be running
# (keep waiting — it may still come up).
DAEMON_HINT_SECONDS = 60.0


def attach_command(queue_id: int) -> list[str]:
    return ["tmux", "-L", managed_sessions.TMUX_SOCKET, "-f", "/dev/null",
            "attach-session", "-t", managed_sessions.exact(queue_id)]


def wait_for_session(queue_id: int, out=sys.stdout, sleep=time.sleep, clock=time.monotonic) -> None:
    started = clock()
    hinted = False
    out.write(_("Starting Claude Code… (the ccc daemon starts it)") + "\n")
    out.flush()
    while not managed_sessions.is_running(queue_id):
        if not hinted and clock() - started >= DAEMON_HINT_SECONDS:
            hinted = True
            out.write(_("Still waiting — is the ccc daemon running? (systemctl --user status ccc)") + "\n")
            out.flush()
        sleep(WAIT_POLL_SECONDS)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in (1, 2) or not args[0].isdigit():
        sys.exit("usage: python -m ccc.attach_cli QUEUE_ID [DETACH_KEY]")
    queue_id = int(args[0])
    detach_key = args[1] if len(args) == 2 else None
    try:
        wait_for_session(queue_id)
    except KeyboardInterrupt:
        return
    managed_sessions.make_transparent(detach_key)
    # A nested client: tmux refuses to attach while TMUX says we're
    # already inside a tmux (ccc's own).
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    command = attach_command(queue_id)
    os.execvpe(command[0], command, env)


if __name__ == "__main__":
    main()
