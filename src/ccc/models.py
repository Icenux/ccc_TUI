from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class QueueStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_RETRY = "waiting_retry"
    BLOCKED_ERROR = "blocked_error"
    # Hit a usage limit with auto-continue off: waits for the user (r / a
    # new message) even after the limit resets.
    PAUSED_LIMIT = "paused_limit"


class ItemStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStatus(str, Enum):
    SUCCESS = "success"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


class DeliveryMode(str, Enum):
    # Runs `claude -p --resume` as a separate headless subprocess. Safe,
    # gives real structured results (tokens/cost/success), but its
    # response never appears in any interactive terminal — only in
    # ccc's own history.
    HEADLESS = "headless"
    # Types the prompt directly into a live interactive session's own
    # tmux pane via `tmux send-keys`, so it visibly continues that
    # terminal's conversation. Riskier (see live_delivery module) and
    # gives no structured result — only "delivered" or "failed".
    LIVE_TERMINAL = "live_terminal"
    # ccc runs an interactive `claude --remote-control` itself (see
    # managed_sessions) and pastes queued prompts into it; the conversation
    # is also reachable from the Claude mobile app / claude.ai.
    REMOTE_CONTROL = "remote_control"
    # Same as REMOTE_CONTROL without --remote-control: a plain interactive
    # Claude Code CLI that ccc runs in tmux and shows next to its own
    # window (tui.cli_pane) — every CLI feature works, slash commands too.
    INTERACTIVE = "interactive"

    @property
    def managed(self) -> bool:
        """An interactive `claude` process that ccc itself keeps running."""
        return self in (DeliveryMode.REMOTE_CONTROL, DeliveryMode.INTERACTIVE)


@dataclass
class Queue:
    id: int
    name: str
    project_path: str
    session_id: str
    status: QueueStatus = QueueStatus.IDLE
    next_retry_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    # True for a queue attached to a session that already existed before
    # ccc touched it (discovered via `claude agents`) — its first
    # prompt must use --resume, never --session-id, since the ID isn't new.
    always_resume: bool = False
    delivery_mode: DeliveryMode = DeliveryMode.HEADLESS
    # Flags passed to `claude` on every run (permissions, model, effort...)
    # for conversations started from the TUI. None = queue predates launch
    # attributes; the runner then uses its legacy flags.
    launch_flags: list[str] | None = None
    # One-shot flags for the first successful run only (--fork-session,
    # --from-pr ...). Cleared once the daemon adopts the session id Claude
    # Code returned for that run.
    first_run_flags: list[str] | None = None
    # Resume by itself once a usage limit resets (else PAUSED_LIMIT).
    auto_continue: bool = True
    # Claude account (ccc.profiles) the conversation runs under; "" = the default one.
    profile: str = ""
    # Manually paused from the Tasks editor: start no further items.
    paused: bool = False
    # Hidden from the TUI lists (still processed by the daemon).
    archived: bool = False
    # When the conversation was last shown in the TUI ("new reply" marker).
    last_seen_at: datetime | None = None
    # Remote-control link printed by the managed interactive session.
    remote_control_url: str | None = None
    # Started by this routine for one run (target "fresh"); archived when done.
    routine_id: int | None = None
    # Claude Code of an interactive conversation is working right now
    # (anything, also what you typed into it), set by the daemon.
    live_busy: bool = False


@dataclass
class QueueItem:
    id: int
    queue_id: int
    prompt_text: str
    position: int
    status: ItemStatus = ItemStatus.PENDING
    added_at: datetime = field(default_factory=datetime.now)
    # A usage limit interrupted this item after it reached the conversation:
    # the retry sends the continue text instead of repeating the prompt.
    continue_on_retry: bool = False
    # Don't start before this (naive local time); None = as soon as possible.
    not_before: datetime | None = None
    # Queued by this routine (ccc.routines); None = by hand.
    routine_id: int | None = None
    # Run on this model instead of the conversation's (a goal loop that
    # stalls switches to a stronger one; headless conversations only).
    model: str | None = None


@dataclass
class PromptRun:
    id: int
    queue_id: int
    queue_item_id: int | None
    prompt_text: str
    started_at: datetime
    finished_at: datetime | None
    status: RunStatus
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    duration_ms: int = 0
    num_turns: int = 0
    result_text: str | None = None
    error_text: str | None = None
    # Auto-commit after a successful headless task in a git repo.
    git_commit: str | None = None
    git_note: str | None = None
    # The run of a review (ccc.reviews), not of a task.
    review_id: int | None = None
    # Its commit was undone by this revert commit (ccc.revert).
    reverted_by: str | None = None
    # A one-shot agent of this workflow run (ccc.workflows), not a task.
    workflow_run_id: int | None = None


@dataclass
class PermissionRequest:
    """Claude (headless, not skip-permissions) asking to use a tool; the
    permission MCP server waits until the user decides in the TUI."""

    id: int
    queue_id: int
    tool_name: str
    input: dict
    created_at: datetime
    status: str = "pending"  # "pending" | "allowed" | "denied"


class RoutineTarget(str, Enum):
    # The task goes into the routine's conversation (same Claude session).
    SAME = "same"
    # Every run starts a new headless conversation in the same folder,
    # archived once it's done (a clean context each time).
    FRESH = "fresh"


@dataclass
class Routine:
    """A prompt ccc queues on a schedule (ccc.routines)."""
    id: int
    queue_id: int  # its conversation (FRESH: the template: folder, flags)
    name: str
    prompt_text: str
    schedule_kind: str  # ccc.routines.KINDS
    schedule_value: str
    cron: str
    target: RoutineTarget = RoutineTarget.SAME
    enabled: bool = True
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: str = ""  # what the last firing did (ccc.routines.STATUS_*)
    skipped: int = 0  # firings skipped because the previous run still went
    created_at: datetime = field(default_factory=datetime.now)


class ReviewStatus(str, Enum):
    PENDING = "pending"  # waits for the conversation to be idle / a limit
    RUNNING = "running"
    DONE = "done"  # has a verdict
    NOTHING = "nothing"  # no changes to review: no model was called
    ERROR = "error"
    STOPPED = "stopped"  # a review-until-pass loop stopped by the user


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"


@dataclass
class Review:
    """A fresh-context review of what a conversation changed (ccc.reviews)."""
    id: int
    queue_id: int
    status: ReviewStatus = ReviewStatus.PENDING
    instructions: str = ""
    # Review until it passes: the findings go back as a task, then the
    # next round reviews again (loop_id = the first review's id).
    until_pass: bool = False
    round: int = 1
    max_rounds: int = 1
    loop_id: int | None = None
    scope: str = ""  # what was reviewed, in words
    base_run_id: int = 0  # it covers the conversation's runs after this one…
    last_run_id: int = 0  # …up to this one
    verdict: Verdict | None = None
    report: str = ""
    error: str = ""
    fix_item_id: int | None = None  # the task queued with its findings
    not_before: datetime | None = None
    created_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    goal_id: int | None = None  # judges this goal (ccc.goals)
    model: str | None = None  # instead of the usual reviewer model


class GoalStatus(str, Enum):
    RUNNING = "running"
    PASSED = "passed"
    STUCK = "stuck"  # the same failure twice in a row
    LIMIT = "limit"  # out of rounds
    STOPPED = "stopped"
    ERROR = "error"


class GoalPhase(str, Enum):
    CODING = "coding"  # the conversation works on its task
    CHECKING = "checking"  # ccc runs the check command
    REVIEWING = "reviewing"  # a reviewer judges the goal


@dataclass
class Goal:
    """Work towards a goal in rounds until a reviewer says it's met (ccc.goals)."""
    id: int
    queue_id: int
    goal: str
    check_command: str = ""
    max_rounds: int = 5
    round: int = 1
    status: GoalStatus = GoalStatus.RUNNING
    phase: GoalPhase = GoalPhase.CODING
    item_id: int | None = None  # this round's task
    review_id: int | None = None  # this round's review
    base_run_id: int = 0  # the conversation's last run before the goal
    last_reason: str = ""  # why the last round didn't pass
    created_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None


class WorkflowStatus(str, Enum):
    RUNNING = "running"
    WAITING = "waiting"  # a gate waits for your answer
    PASSED = "passed"
    FAILED = "failed"  # out of rounds, or rejected at a gate
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class WorkflowRun:
    """One run of a workflow template in a conversation (ccc.workflows)."""
    id: int
    queue_id: int
    name: str  # the template's
    template: dict  # a snapshot: later edits of the template don't change a run
    task: str
    check_command: str = ""
    max_rounds: int = 3
    status: WorkflowStatus = WorkflowStatus.RUNNING
    step: int = 0  # index into template["steps"]
    round: int = 1
    base_run_id: int = 0
    item_id: int | None = None  # the code step's task
    plan: str = ""  # the plan step's output
    findings: str = ""  # what the last failed check / review / arbiter said
    last_reason: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None


@dataclass
class WorkflowEvent:
    """What one step of a run did, for the history (ccc.workflows)."""
    id: int
    run_id: int
    step: int
    round: int
    kind: str
    name: str
    status: str  # "running" | "done" | "error"
    verdict: str = ""
    output: str = ""
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Device:
    """A phone (or other client) signed in to the API (ccc.api): it has its
    own token, of which only the SHA-256 is kept."""
    id: int
    name: str
    token_sha256: str
    created_at: datetime = field(default_factory=datetime.now)
    last_seen_at: datetime | None = None
    last_ip: str = ""
    # Signed out: when, and how (REVOKED_BY_*). The row stays as history; its token no longer works.
    revoked_at: datetime | None = None
    revoked_by: str = ""
    # What the device may do (ROLES, ccc.api_roles); devices from before roles are "admin", as they were.
    role: str = "admin"


ROLE_READ = "read"  # only looks: lists, conversations, diffs
ROLE_OPERATE = "operate"  # also steers the conversations that exist: messages, approvals, queue
ROLE_ADMIN = "admin"  # also starts conversations, types into terminals, updates ccc, changes settings and devices
ROLES = (ROLE_READ, ROLE_OPERATE, ROLE_ADMIN)  # weakest first


REVOKED_BY_APP = "app"  # signed out from an app (this phone's own, or another one's)
REVOKED_BY_COMPUTER = "computer"  # `ccc api revoke` or the Devices list
REVOKED_BY_TOKEN = "token"  # `ccc api token --new`: every token before it stopped working
