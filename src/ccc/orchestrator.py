from __future__ import annotations

import asyncio
import json
import os
import re
import logging
import sqlite3
import sys
import uuid
from types import SimpleNamespace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from ccc import folders, gitops, managed_sessions
from ccc.i18n import _
from ccc.launch_options import (
    FORK,
    SESSION_CONTINUE,
    SESSION_FROM_PR,
    SESSION_NEW,
    persistent_flags,
    session_mode,
)
from ccc.limits import exhausted_until, retry_time_after_rate_limit
from ccc.live_delivery import deliver_to_live_session
from ccc.notify import CAT_ATTENTION, CAT_LIMITS, CAT_QUEUE, CAT_STARTED
from ccc.models import DeliveryMode, ItemStatus, PromptRun, Queue, QueueItem, QueueStatus, RunStatus
from ccc.retry import next_backoff_seconds, parse_reset_time
from ccc.runner import LEGACY_FLAGS, RunResult, execute
from ccc.session_discovery import discover_sessions, session_ready_for_input
from ccc.storage import Storage
from ccc.transcript import find_transcript_path, last_human_text, list_sessions_for_directory, session_recorded_in
from ccc.usage_api import get_pro_usage

logger = logging.getLogger(__name__)

RunFn = Callable[[str, str, str, bool, list[str] | None, list[str] | None], Awaitable[RunResult]]

# How often an idle/blocked queue re-checks storage even without an
# in-process notify(). This is what makes it safe for another process (the
# TUI, or `ccc add`) to enqueue a prompt or clear a blocked queue
# without the daemon needing any cross-process signaling mechanism.
POLL_INTERVAL_SECONDS = 5.0
START_QUIET_SECONDS = 600.0  # "conversation started" only after this long without a task of it

# Sent instead of repeating a prompt that a usage limit interrupted after
# it had reached the conversation. Overridable via the "continue_text"
# setting (`ccc config continue-text`).
DEFAULT_CONTINUE_TEXT = "Pokračuj tam, kde jsi skončil."

# Remote-control conversations: how often the daemon checks its managed
# interactive process (running? trust prompt? link? hit a limit?) — each
# check runs `claude agents`, so not on every 5s poll.
MAINTENANCE_INTERVAL_SECONDS = 10.0
# After pasting a prompt, wait up to this long for Claude to report busy
# before considering the next one (it flips from idle a moment later).
DELIVERY_SETTLE_SECONDS = 15.0
# The folder-trust dialog is only accepted this soon after the daemon
# started the process, and at most once per start.
TRUST_CONFIRM_WINDOW_SECONDS = 60.0
# Fallback wait when a managed session hits a limit and usage is unknown.
MANAGED_LIMIT_FALLBACK = timedelta(minutes=15)


def enqueue_prompt(storage: Storage, project_path: str, prompt_text: str, name: str | None = None) -> Queue:
    """Add a prompt to a project's queue, creating the queue if needed.
    Pure storage operation — does not start or notify any execution
    engine. Safe to call from a process that never runs an Orchestrator
    (the TUI, the `ccc add` CLI) alongside a daemon process that
    does; the daemon picks the new row up on its next poll.

    Raises ValueError if project_path isn't an existing directory when
    creating a *new* queue — a typo'd path (e.g. a transposed letter)
    would otherwise sit "running" forever with no visible error once the
    daemon tries to use it as a subprocess cwd. Adding to an
    already-existing queue never re-checks the path."""
    queue = storage.get_queue_by_path(project_path)
    if queue is None:
        if not Path(project_path).is_dir():
            raise ValueError(_("'{path}' is not an existing directory").format(path=project_path))
        queue = storage.create_queue(
            name or Path(project_path).name or project_path,
            project_path,
            str(uuid.uuid4()),
        )
    storage.add_queue_item(queue.id, prompt_text)
    return queue


def send_message(storage: Storage, queue_id: int, text: str) -> QueueItem:
    """Queue a message in a conversation (the TUI's message box, the API).
    Writing to a conversation paused at a limit means "go on". Pure
    storage write — the daemon picks it up on its next poll."""
    item = storage.add_queue_item(queue_id, text.strip())
    if storage.get_queue(queue_id).status == QueueStatus.PAUSED_LIMIT:
        storage.update_queue_status(queue_id, QueueStatus.IDLE)
    return item


def attach_to_session(
    storage: Storage, session_id: str, cwd: str, name: str, prompt_text: str
) -> Queue:
    """Queue a prompt against a Claude Code session that already exists —
    typically one found via session_discovery.discover_sessions() (a live
    interactive or background session on this machine). Unlike
    enqueue_prompt(), this never generates a fresh session_id and always
    marks the queue always_resume=True, since the very first prompt sent
    through it must use --resume (the session already has history;
    --session-id would try to create a new one with a colliding ID).
    Pure storage operation, same process-safety notes as enqueue_prompt()."""
    queue = storage.get_queue_by_session_id(session_id)
    if queue is None:
        queue = storage.create_queue(
            name, cwd, session_id, always_resume=True, delivery_mode=DeliveryMode.LIVE_TERMINAL
        )
    storage.add_queue_item(queue.id, prompt_text)
    return queue


# What Claude Code is given as `--resume <id>` / `--from-pr <value>`: never something that reads as another option.
SESSION_ID_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z_-]{0,99}")
PULL_REQUEST_RE = re.compile(r"\d{1,9}|https?://[^\s]+")


def start_conversation(
    storage, project_path, selection, *, name=None, session_id=None, pr=None, auto_continue=True,
    remote_control=False, interactive=True, worktree=False,
) -> Queue:
    """Create the queue behind a conversation started from the TUI's
    New conversation dialog (see launch_options for what selection
    holds): an interactive Claude Code CLI (shown in ccc, see
    tui.cli_pane), with --remote-control when asked; interactive=False
    gives a headless `claude -p` queue. Raises ValueError with a message
    fit to show the user."""
    if not Path(project_path).is_dir():
        raise ValueError(_("'{path}' is not an existing directory").format(path=project_path))
    mode = session_mode(selection)
    if session_id and not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError(_("That is not a session id."))
    if pr and pr.strip() and not PULL_REQUEST_RE.fullmatch(pr.strip()):
        raise ValueError(_("A pull request is a number or a URL."))
    fork = FORK in selection and mode != SESSION_NEW
    flags = persistent_flags(selection)
    # A name goes to Claude Code as an argument (--remote-control NAME): never one that reads as another option.
    name = ((name or "").strip() or Path(project_path).name or project_path)
    name = re.sub(r"^[\s-]+", "", name).strip() or "conversation"
    if worktree:
        # Claude Code keeps sessions per folder: an earlier one can't be
        # continued from a new worktree.
        if mode not in (SESSION_NEW, SESSION_FROM_PR):
            raise ValueError(_("An own worktree needs a new session (or one from a pull request)."))
        # ccc runs git (here: the checkout) only where Claude may run any
        # command anyway — see gitops.
        if not gitops.claude_can_run_commands(SimpleNamespace(launch_flags=flags)):
            raise ValueError(_("An own worktree needs “Skip all permission checks” (ccc runs git only there)."))
        project_path = gitops.create_worktree(project_path, name)
    if remote_control:
        delivery = DeliveryMode.REMOTE_CONTROL
    else:
        delivery = DeliveryMode.INTERACTIVE if interactive else DeliveryMode.HEADLESS

    if mode == SESSION_NEW:
        return storage.create_queue(
            name, project_path, str(uuid.uuid4()), launch_flags=flags, auto_continue=auto_continue,
            delivery_mode=delivery,
        )

    if mode == SESSION_FROM_PR:
        pr = (pr or "").strip()
        if not pr:
            raise ValueError(_("Enter a pull request number or URL."))
        first = ["--from-pr", pr] + (["--fork-session"] if fork else [])
        return storage.create_queue(
            name, project_path, str(uuid.uuid4()), launch_flags=flags, first_run_flags=first,
            auto_continue=auto_continue, delivery_mode=delivery,
        )

    if mode == SESSION_CONTINUE:
        sessions = list_sessions_for_directory(project_path)
        if not sessions:
            raise ValueError(_("There is no previous conversation in this directory."))
        session_id = sessions[0].session_id
    if not session_id:
        raise ValueError(_("Select a conversation to resume."))
    if not SESSION_ID_RE.fullmatch(session_id):  # also one found on disk (SESSION_CONTINUE)
        raise ValueError(_("That is not a session id."))
    # A session is resumed only from the folder Claude Code recorded it in: not by naming an id from elsewhere.
    if storage.get_queue_by_session_id(session_id) is None and not (
            session_id in {s.session_id for s in list_sessions_for_directory(project_path)}
            and session_recorded_in(session_id, project_path)):
        raise ValueError(_("That session wasn't recorded in this folder."))

    if fork:
        return storage.create_queue(
            name, project_path, str(uuid.uuid4()), launch_flags=flags,
            first_run_flags=["--resume", session_id, "--fork-session"],
            auto_continue=auto_continue, delivery_mode=delivery,
        )
    existing = storage.get_queue_by_session_id(session_id)
    if existing is not None:
        if os.path.realpath(existing.project_path) != os.path.realpath(project_path):
            # The folder was checked, the session lives elsewhere: not a way round the folder check (#160).
            raise ValueError(_("That session belongs to a conversation in another folder."))
        return existing
    return storage.create_queue(
        name, project_path, session_id, always_resume=True, launch_flags=flags, auto_continue=auto_continue,
        delivery_mode=delivery,
    )


def with_model(flags: list[str] | None, model: str) -> list[str]:
    """The launch flags with --model set to `model` (a task's own model)."""
    flags = list(flags if flags is not None else LEGACY_FLAGS)
    if "--model" in flags[:-1]:
        flags[flags.index("--model") + 1] = model
    else:
        flags += ["--model", model]
    return flags


RESERVE_KEY = "usage_reserve"
# The reserves offered in Settings (TUI, app).
RESERVE_CHOICES = [50, 60, 70, 75, 80, 85, 90, 95]


def usage_reserve(storage) -> int | None:
    """The user's usage reserve threshold in percent, or None when off."""
    raw = storage.get_setting(RESERVE_KEY)
    try:
        value = int(raw) if raw else None
    except ValueError:
        return None
    return value if value is not None and 0 < value < 100 else None


def ccc_mcp_flags(queue_id: int, db_path: str) -> list[str]:
    """Attach ccc's MCP server (permission_mcp), whose `approve` tool
    permission_tool_flags uses."""
    config = {
        "mcpServers": {
            "ccc": {
                "command": sys.executable,
                # -I: isolated mode — the server starts in the project dir, which
                # Claude may write to; never import modules from there.
                "args": ["-I", "-m", "ccc.permission_mcp"],
                "env": {"CCC_DB": str(db_path), "CCC_QUEUE_ID": str(queue_id)},
            }
        }
    }
    return ["--mcp-config", json.dumps(config)]


def permission_tool_flags(queue_id: int, db_path: str) -> list[str]:
    """ccc's MCP server as the permission prompt tool, so tool approvals
    wait for the user instead of being denied in -p mode."""
    return [*ccc_mcp_flags(queue_id, db_path), "--permission-prompt-tool", "mcp__ccc__approve"]


def _inside_git_repo(path: str) -> bool:
    """Just looks for a .git entry upwards — runs no git command."""
    current = Path(path).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return True
    return False


class QueueRuntime:
    def __init__(self, storage: Storage, queue: Queue, run_fn: RunFn = execute, notifier=None):
        self.storage = storage
        self.queue = queue
        self.run_fn = run_fn
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._backoff_seconds: int | None = None
        # Anything with .notify(text) (Telegram in the daemon); None = silent.
        self.notifier = notifier
        # Remote-control conversations: last maintenance time and whether the
        # managed session was blocked on a limit at the previous check.
        self._last_maintenance = 0.0
        self._managed_was_blocked = False
        # Loop time when the daemon last started the managed process, and
        # whether the trust dialog may still be accepted for that start.
        self._managed_started_at: float | None = None
        self._trust_confirm_pending = False

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        self._task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        # Last-resort safety net: _loop() already catches everything it
        # can attribute to a specific item, but if something still
        # escapes (a bug outside that try/except), log it instead of the
        # queue silently going stuck forever with no trace anywhere.
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Queue runtime for %s crashed and stopped: %r", self.queue.project_path, exc)

    def notify(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            queue = self.storage.find_queue(self.queue.id)
            if queue is None:
                logger.info("Queue %s was deleted, stopping its runtime", self.queue.id)
                if self.queue.delivery_mode.managed:
                    await asyncio.to_thread(managed_sessions.stop, self.queue.id)
                return
            self.queue = queue

            if queue.delivery_mode.managed:
                if queue.archived:
                    # Archiving frees the interactive process; nothing runs.
                    if await asyncio.to_thread(managed_sessions.is_running, queue.id):
                        await asyncio.to_thread(managed_sessions.stop, queue.id)
                    await self._wait_for_wake_or_poll(POLL_INTERVAL_SECONDS)
                    continue
                await self._maintain_managed_session(queue)
                queue = self.storage.find_queue(queue.id)
                if queue is None:
                    continue
                self.queue = queue

            if queue.status in (QueueStatus.BLOCKED_ERROR, QueueStatus.PAUSED_LIMIT) or queue.paused:
                # Waiting for the user (r / a new message / unpause in TUI).
                await self._wait_for_wake_or_poll(POLL_INTERVAL_SECONDS)
                continue

            if queue.status == QueueStatus.WAITING_RETRY and queue.next_retry_at is not None:
                remaining = (queue.next_retry_at - datetime.now()).total_seconds()
                if remaining > 0:
                    # Sleep in slices so changes made from the TUI are seen.
                    await self._wait_for_wake_or_poll(min(remaining, POLL_INTERVAL_SECONDS))
                    continue
                # Reset time reached: confirm with fresh usage figures.
                until = exhausted_until(await asyncio.to_thread(get_pro_usage, True), datetime.now())
                if until is not None:
                    self._hold_for_limit(queue, until)
                    continue
                self.storage.update_queue_status(queue.id, QueueStatus.IDLE)
                if self.storage.next_pending_item(queue.id) is not None:
                    self._notify(_("▶ {name}: usage limit reset — continuing.").format(name=queue.name), CAT_LIMITS)
                continue

            if self.storage.next_pending_item(queue.id) is None:
                if queue.status != QueueStatus.IDLE:
                    self.storage.update_queue_status(queue.id, QueueStatus.IDLE)
                await self._wait_for_wake_or_poll(POLL_INTERVAL_SECONDS)
                continue

            upcoming = self.storage.next_pending_item(queue.id)
            if upcoming.not_before is not None:
                remaining = (upcoming.not_before - datetime.now()).total_seconds()
                if remaining > 0:
                    # Scheduled for later; it keeps its place, so tasks
                    # queued behind it wait too.
                    if queue.status != QueueStatus.IDLE:
                        self.storage.update_queue_status(queue.id, QueueStatus.IDLE)
                    await self._wait_for_wake_or_poll(min(remaining, POLL_INTERVAL_SECONDS))
                    continue

            if queue.delivery_mode == DeliveryMode.HEADLESS or queue.delivery_mode.managed:
                # Don't even start Claude while a limit window is used up —
                # or above the reserve the user keeps for themselves.
                usage = await asyncio.to_thread(get_pro_usage)
                until = exhausted_until(usage, datetime.now())
                if until is not None:
                    self._hold_for_limit(queue, until)
                    continue
                reserve = usage_reserve(self.storage)
                if reserve is not None:
                    until = exhausted_until(usage, datetime.now(), threshold=reserve)
                    if until is not None:
                        self._hold_for_reserve(queue, until, reserve)
                        continue

            if queue.delivery_mode == DeliveryMode.HEADLESS and folders.running_neighbour(self.storage, queue):
                # Another conversation runs a task in this work tree: take
                # turns (no await between this check and the claim below,
                # so two runtimes can't both pass it).
                await self._wait_for_wake_or_poll(POLL_INTERVAL_SECONDS)
                continue

            item = self.storage.claim_next_pending_item(queue.id)
            if item is None:
                continue

            self._announce_start(queue, item)
            self.storage.update_queue_status(queue.id, QueueStatus.RUNNING)

            try:
                await self._process_item(queue, item)
            except Exception as exc:
                # Anything unexpected here (bad cwd, a bug in run_fn, a
                # storage hiccup) must never leave the queue silently
                # stuck at "running" forever with no trace — surface it
                # the same way a normal hard error would, including in
                # the queue's own run history (not just last_error).
                logger.exception(
                    "Unhandled error running queue '%s' (%s)", queue.name, queue.project_path
                )
                self._record_error_run(queue, item, f"{type(exc).__name__}: {exc}")

    def _notify(self, text: str, category: str | None = None) -> None:
        if self.notifier is not None:
            self.notifier.notify(text, category)

    def _announce_start(self, queue: Queue, item: QueueItem) -> None:
        """Tell that a conversation starts working after a quiet time (START_QUIET_SECONDS);
        a routine's task is told by the routine."""
        if self.notifier is None or item.routine_id is not None \
                or not self.notifier.configured(CAT_STARTED):
            return
        runs = self.storage.runs_for_queue(queue.id)
        if runs:
            last = runs[0].finished_at or runs[0].started_at
            if (datetime.now() - last).total_seconds() < START_QUIET_SECONDS:
                return
        self._notify(_("▶ {name}: starts working — {prompt}").format(
            name=queue.name, prompt=" ".join(item.prompt_text.split())[:120]), CAT_STARTED)

    def _hold_for_reserve(self, queue: Queue, until: datetime, reserve: int) -> None:
        """Usage is above the user's reserve: wait for the window's reset
        and carry on by itself (a preference, not a hard limit)."""
        note = f"usage reserve {reserve}%"
        already = queue.status == QueueStatus.WAITING_RETRY and queue.last_error == note
        self.storage.update_queue_status(queue.id, QueueStatus.WAITING_RETRY, next_retry_at=until, last_error=note)
        if not already:
            self._notify(_("‖ {name}: usage above your {reserve}% reserve — waiting until {time}.").format(
                name=queue.name, reserve=reserve, time=f"{until:%H:%M}"), CAT_LIMITS)

    def _hold_for_limit(self, queue: Queue, until: datetime) -> None:
        """Usage limit: wait for the reset by itself (auto-continue) or
        stop and wait for the user."""
        status = QueueStatus.WAITING_RETRY if queue.auto_continue else QueueStatus.PAUSED_LIMIT
        self.storage.update_queue_status(queue.id, status, next_retry_at=until)
        if queue.status == status:
            return  # still the same wait (e.g. re-checked at reset time)
        if status == QueueStatus.WAITING_RETRY:
            self._notify(_("⏸ {name}: usage limit — waiting until {time}, then it continues by itself.").format(
                name=queue.name, time=f"{until:%H:%M}"), CAT_LIMITS)
        else:
            self._notify(
                _("✋ {name} needs you: paused at the usage limit (resets {time}). "
                  "Press r or send a message in ccc to continue.").format(name=queue.name, time=f"{until:%H:%M}"),
                CAT_ATTENTION,
            )

    async def _process_item(self, queue: Queue, item: QueueItem) -> None:
        if queue.delivery_mode == DeliveryMode.LIVE_TERMINAL:
            await self._process_live_terminal_item(queue, item)
            return
        if queue.delivery_mode.managed:
            await self._process_managed_item(queue, item)
            return

        resume = queue.always_resume or self._has_run_before()
        if item.continue_on_retry:
            text = self.storage.get_setting("continue_text", DEFAULT_CONTINUE_TEXT) or DEFAULT_CONTINUE_TEXT
        else:
            text = item.prompt_text
        # Git only where Claude may run commands anyway — see gitops.
        git_allowed = gitops.claude_can_run_commands(queue)
        before = await asyncio.to_thread(gitops.snapshot, queue.project_path) if git_allowed else None
        flags = with_model(queue.launch_flags, item.model) if item.model else queue.launch_flags
        if not git_allowed:
            # Restricted permission mode: tool approvals go to the user in
            # the TUI instead of being denied automatically.
            flags = list(flags or []) + permission_tool_flags(queue.id, self.storage.db_path)
        else:
            flags = list(flags if flags is not None else LEGACY_FLAGS)
        started_at = datetime.now()
        result = await self.run_fn(
            queue.project_path, queue.session_id, text, resume, flags, queue.first_run_flags
        )
        finished_at = datetime.now()
        git_commit = git_note = None
        if not git_allowed and result.status == RunStatus.SUCCESS and _inside_git_repo(queue.project_path):
            git_note = _("not committed: auto-commit only runs in 'Skip all permission checks' conversations")
        busy = None
        if before is not None and result.status == RunStatus.SUCCESS:
            # Never sweep another conversation's half-written work into this commit.
            busy = await asyncio.to_thread(folders.busy_neighbour, self.storage, queue)
        if busy is not None:
            git_note = _("not committed: another conversation ({name}) works in this folder").format(name=busy.name)
        elif before is not None and result.status == RunStatus.SUCCESS:
            outcome = await asyncio.to_thread(
                gitops.commit_task, queue.project_path, before, item.prompt_text, queue.name
            )
            git_commit, git_note = outcome.commit, outcome.note

        run_id = self.storage.insert_prompt_run(
            PromptRun(
                id=0,
                queue_id=queue.id,
                queue_item_id=item.id,
                prompt_text=text,
                started_at=started_at,
                finished_at=finished_at,
                status=result.status,
                cost_usd=result.cost_usd,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cache_read_tokens=result.cache_read_tokens,
                cache_creation_tokens=result.cache_creation_tokens,
                duration_ms=result.duration_ms,
                num_turns=result.num_turns,
                result_text=result.result_text,
                error_text=result.error_text,
                git_commit=git_commit,
                git_note=git_note,
            )
        )
        for model_name, tokens in result.model_usage.items():
            self.storage.insert_run_model_usage(
                run_id,
                model_name,
                input_tokens=tokens.get("input_tokens", 0),
                output_tokens=tokens.get("output_tokens", 0),
                cache_read_tokens=tokens.get("cache_read_tokens", 0),
                cache_creation_tokens=tokens.get("cache_creation_tokens", 0),
            )

        if result.status == RunStatus.SUCCESS:
            self.storage.set_item_status(item.id, ItemStatus.DONE)
            self._backoff_seconds = None
            if self.storage.next_pending_item(queue.id) is None:
                reply = (result.result_text or "").strip()
                self._notify(_("✅ {name}: queue finished.").format(name=queue.name) + (f"\n\n{reply[:300]}" if reply else ""),
                             CAT_QUEUE)
            if queue.first_run_flags is not None:
                self._adopt_session(queue, result.session_id or queue.session_id)
            return

        if result.status == RunStatus.RATE_LIMITED:
            self.storage.set_item_status(item.id, ItemStatus.PENDING)
            reached = await asyncio.to_thread(last_human_text, queue.session_id)
            if reached is not None and reached.strip() == text.strip():
                # Claude had started on it — resume rather than repeat it.
                self.storage.set_item_continue_on_retry(item.id, True)
            now = datetime.now()
            retry_at = retry_time_after_rate_limit(await asyncio.to_thread(get_pro_usage, True), now)
            if retry_at is None:
                retry_at = parse_reset_time(result.error_text or "")
            if retry_at is None:
                self._backoff_seconds = next_backoff_seconds(self._backoff_seconds)
                retry_at = now + timedelta(seconds=self._backoff_seconds)
            self._hold_for_limit(queue, retry_at)
            return

        self._fail_item(queue, item, result.error_text)

    async def _process_live_terminal_item(self, queue: Queue, item: QueueItem) -> None:
        """Deliver a prompt directly into a live interactive session's tmux
        pane, so it visibly continues that terminal's own conversation
        (see live_delivery module for the mechanism and its risks — this
        is the mode the user explicitly chose over the safer headless
        default). Waits, polling like a rate-limited retry, until the
        target session reports itself idle and not blocked before
        attempting delivery — but a session that's genuinely gone, or a
        tmux send-keys failure, is a real (non-retryable) error."""
        while True:
            sessions = discover_sessions()
            session = next((s for s in sessions if s.session_id == queue.session_id), None)
            if session is None:
                self._record_error_run(queue, item, _("session is no longer running (closed or crashed)"))
                return
            if session_ready_for_input(session):
                break
            await self._wait_for_wake_or_poll(POLL_INTERVAL_SECONDS)

        result = deliver_to_live_session(queue.session_id, item.prompt_text)
        if not result.delivered:
            self._record_error_run(queue, item, result.error or _("delivery to live terminal failed"))
            return

        now = datetime.now()
        self.storage.insert_prompt_run(
            PromptRun(
                id=0,
                queue_id=queue.id,
                queue_item_id=item.id,
                prompt_text=item.prompt_text,
                started_at=now,
                finished_at=now,
                status=RunStatus.SUCCESS,
                result_text=_("Delivered to the live terminal — response appears there, not in ccc."),
            )
        )
        self.storage.set_item_status(item.id, ItemStatus.DONE)

    # --- remote-control conversations (managed interactive sessions) -------

    async def _managed_agent(self, queue: Queue):
        """This conversation's interactive process as `claude agents` sees
        it (matched by its tmux pane's pid, else by session id)."""
        pid = await asyncio.to_thread(managed_sessions.pane_pid, queue.id)
        sessions = await asyncio.to_thread(discover_sessions)
        return next(
            (s for s in sessions if (pid is not None and s.pid == pid) or s.session_id == queue.session_id),
            None,
        )

    async def _maintain_managed_session(self, queue: Queue, force: bool = False) -> None:
        now = asyncio.get_running_loop().time()
        if not force and now - self._last_maintenance < MAINTENANCE_INTERVAL_SECONDS:
            return
        self._last_maintenance = now

        if not await asyncio.to_thread(managed_sessions.is_running, queue.id):
            has_transcript = find_transcript_path(queue.session_id) is not None
            command = managed_sessions.build_interactive_command(queue, has_transcript)
            logger.info("Starting interactive session for '%s'", queue.name)
            await asyncio.to_thread(managed_sessions.start, queue, command)
            self._managed_was_blocked = False
            self._managed_started_at = asyncio.get_running_loop().time()
            self._trust_confirm_pending = True
            return

        pane = await asyncio.to_thread(managed_sessions.capture, queue.id)
        in_startup_window = (
            self._managed_started_at is not None
            and asyncio.get_running_loop().time() - self._managed_started_at <= TRUST_CONFIRM_WINDOW_SECONDS
        )
        if self._trust_confirm_pending and in_startup_window and managed_sessions.needs_trust_confirmation(pane):
            # Only the folder-trust dialog, only right after our own start,
            # only once. Tool permission prompts are never answered here.
            self._trust_confirm_pending = False
            await asyncio.to_thread(managed_sessions.confirm, queue.id)
        url = managed_sessions.parse_remote_url(pane)
        if url and url != queue.remote_control_url:
            self.storage.set_remote_control_url(queue.id, url)

        session = await self._managed_agent(queue)
        if session is None:
            return
        # What you typed into Claude Code yourself shows in the list too.
        self.storage.set_live_busy(queue.id, session.status == "busy")
        if session.session_id != queue.session_id:
            # Fork / from-PR, but also a plain --resume: the process writes to a new session id,
            # and the app (API) must read that transcript, not the old one.
            self._adopt_session(queue, session.session_id, blocking=queue.first_run_flags is not None)
        blocked = session.state == "blocked"
        if blocked and not self._managed_was_blocked and queue.status not in (
            QueueStatus.WAITING_RETRY,
            QueueStatus.PAUSED_LIMIT,
        ):
            # Hit a usage limit mid-work: queue a "continue" ahead of
            # everything and wait for the reset (or for the user).
            text = self.storage.get_setting("continue_text", DEFAULT_CONTINUE_TEXT) or DEFAULT_CONTINUE_TEXT
            self.storage.add_queue_item_front(queue.id, text)
            now_dt = datetime.now()
            retry_at = retry_time_after_rate_limit(
                await asyncio.to_thread(get_pro_usage, True), now_dt
            ) or (now_dt + MANAGED_LIMIT_FALLBACK)
            self._hold_for_limit(queue, retry_at)
        self._managed_was_blocked = blocked

    async def _process_managed_item(self, queue: Queue, item: QueueItem) -> None:
        """Paste a queued prompt into the conversation's managed interactive
        process once it's idle (it may be busy with a message sent from the
        phone / claude.ai)."""
        while True:
            current = self.storage.find_queue(queue.id)
            if current is None or current.archived or current.paused:
                self.storage.set_item_status(item.id, ItemStatus.PENDING)
                return
            if not await asyncio.to_thread(managed_sessions.is_running, queue.id):
                await self._maintain_managed_session(current, force=True)
            else:
                # Keep maintaining while waiting: Claude may sit on the
                # folder-trust dialog (not idle) — answered only here, or
                # this wait and the dialog wait for each other forever.
                await self._maintain_managed_session(current)
                session = await self._managed_agent(current)
                if session is not None and session.status == "idle":
                    break
            await self._wait_for_wake_or_poll(POLL_INTERVAL_SECONDS)

        text = item.prompt_text
        if item.continue_on_retry:
            text = self.storage.get_setting("continue_text", DEFAULT_CONTINUE_TEXT) or DEFAULT_CONTINUE_TEXT
        result = await asyncio.to_thread(managed_sessions.paste_and_submit, queue.id, text)
        if not result.delivered:
            self._record_error_run(queue, item, result.error or _("delivery to the interactive session failed"))
            return
        now = datetime.now()
        self.storage.insert_prompt_run(
            PromptRun(
                id=0,
                queue_id=queue.id,
                queue_item_id=item.id,
                prompt_text=text,
                started_at=now,
                finished_at=now,
                status=RunStatus.SUCCESS,
                result_text=_("Delivered to the interactive session — the reply is in the transcript."),
            )
        )
        self.storage.set_item_status(item.id, ItemStatus.DONE)
        # Give Claude a moment to pick it up so the next item isn't pasted
        # while it still reports the previous idle state.
        deadline = asyncio.get_running_loop().time() + DELIVERY_SETTLE_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            session = await self._managed_agent(queue)
            if session is None or session.status != "idle":
                break
            await asyncio.sleep(min(1.0, DELIVERY_SETTLE_SECONDS))

    def _adopt_session(self, queue: Queue, session_id: str, blocking: bool = True) -> None:
        """After the first successful run of a fork / from-PR conversation,
        switch the queue from its placeholder id to the session Claude Code
        actually created, so later messages --resume that one."""
        try:
            self.storage.set_queue_session(queue.id, session_id)
        except sqlite3.IntegrityError:
            if not blocking:
                logger.warning("session %s already belongs to another conversation; keeping '%s' on its own", session_id, queue.name)
                return
            self.storage.update_queue_status(
                queue.id,
                QueueStatus.BLOCKED_ERROR,
                last_error=_("session {session} already has a conversation in ccc").format(session=session_id),
            )

    def _record_error_run(self, queue: Queue, item: QueueItem, error_text: str) -> None:
        now = datetime.now()
        self.storage.insert_prompt_run(
            PromptRun(
                id=0,
                queue_id=queue.id,
                queue_item_id=item.id,
                prompt_text=item.prompt_text,
                started_at=now,
                finished_at=now,
                status=RunStatus.ERROR,
                error_text=error_text,
            )
        )
        self._fail_item(queue, item, error_text)

    def _fail_item(self, queue: Queue, item: QueueItem, error_text: str | None) -> None:
        self.storage.set_item_status(item.id, ItemStatus.FAILED)
        self.storage.update_queue_status(queue.id, QueueStatus.BLOCKED_ERROR, last_error=error_text)
        self._notify(_("✖ {name}: stopped on an error — {error}").format(
            name=queue.name, error=(error_text or _("unknown error"))[:300]), CAT_QUEUE)

    def _has_run_before(self) -> bool:
        return len(self.storage.runs_for_queue(self.queue.id)) > 0

    async def _wait_for_wake_or_poll(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            self._wake.clear()
        except asyncio.TimeoutError:
            pass


class Orchestrator:
    def __init__(self, storage: Storage, run_fn: RunFn = execute, notifier=None):
        self.storage = storage
        self.run_fn = run_fn
        self.notifier = notifier
        self.runtimes: dict[int, QueueRuntime] = {}
        self._discovery_task: asyncio.Task | None = None

    async def start_all(self) -> None:
        """Start executing every existing queue, then keep discovering new
        ones (created later by `ccc add` or the TUI, in this or
        another process) in the background. Only ONE process (the daemon)
        should ever call this: it resets any item left 'running' back to
        'pending' as crash recovery, which would wrongly steal an item
        that's legitimately mid-execution in an already-running daemon.
        Other processes (the TUI, `ccc add`) must only use
        enqueue_prompt() — never construct an Orchestrator and call this."""
        self.storage.reset_running_items_to_pending()
        for queue in self.storage.list_queues():
            self._start_runtime(queue)
        self._discovery_task = asyncio.create_task(self._discover_new_queues_loop())

    async def _discover_new_queues_loop(self) -> None:
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            for queue in self.storage.list_queues():
                if queue.id not in self.runtimes:
                    self._start_runtime(queue)

    def _start_runtime(self, queue: Queue) -> QueueRuntime:
        runtime = QueueRuntime(self.storage, queue, self.run_fn, self.notifier)
        self.runtimes[queue.id] = runtime
        runtime.start()
        return runtime

    def get_or_create_queue(self, project_path: str, name: str | None = None) -> Queue:
        """Same path-existence check as enqueue_prompt() — a queue must
        never be created for a directory that doesn't exist."""
        existing = self.storage.get_queue_by_path(project_path)
        if existing:
            if existing.id not in self.runtimes:
                self._start_runtime(existing)
            return existing
        if not Path(project_path).is_dir():
            raise ValueError(_("'{path}' is not an existing directory").format(path=project_path))
        queue = self.storage.create_queue(
            name or project_path.rstrip("/").split("/")[-1],
            project_path,
            str(uuid.uuid4()),
        )
        self._start_runtime(queue)
        return queue

    def add_prompt(self, project_path: str, prompt_text: str) -> None:
        queue = self.get_or_create_queue(project_path)
        self.storage.add_queue_item(queue.id, prompt_text)
        self.runtimes[queue.id].notify()

    async def stop_all(self) -> None:
        if self._discovery_task:
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except asyncio.CancelledError:
                pass
        await asyncio.gather(*(rt.stop() for rt in self.runtimes.values()))
