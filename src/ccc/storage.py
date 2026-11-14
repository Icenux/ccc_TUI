from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from ccc.i18n import _
from ccc.models import (
    Device,
    DeliveryMode,
    Goal,
    GoalPhase,
    GoalStatus,
    ItemStatus,
    PermissionRequest,
    PromptRun,
    Queue,
    QueueItem,
    QueueStatus,
    Review,
    ReviewStatus,
    Routine,
    RoutineTarget,
    RunStatus,
    Verdict,
    WorkflowEvent,
    WorkflowRun,
    WorkflowStatus,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS queues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    project_path TEXT NOT NULL,
    session_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'idle',
    next_retry_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    always_resume INTEGER NOT NULL DEFAULT 0,
    delivery_mode TEXT NOT NULL DEFAULT 'headless',
    launch_flags TEXT,
    first_run_flags TEXT,
    auto_continue INTEGER NOT NULL DEFAULT 1,
    paused INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    remote_control_url TEXT,
    live_busy INTEGER NOT NULL DEFAULT 0,
    live_finished_at TEXT,
    profile TEXT NOT NULL DEFAULT '',
    context_percent REAL,
    context_tokens INTEGER,
    context_window INTEGER,
    context_model TEXT
);

CREATE TABLE IF NOT EXISTS queue_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    prompt_text TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    added_at TEXT NOT NULL,
    continue_on_retry INTEGER NOT NULL DEFAULT 0,
    not_before TEXT
);

-- Prompts queued on a schedule (ccc.routines). queue_id is the routine's
-- conversation; target 'fresh' starts a new conversation per run.
CREATE TABLE IF NOT EXISTS routines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    name TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    schedule_kind TEXT NOT NULL,
    schedule_value TEXT NOT NULL,
    cron TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT 'same',
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run_at TEXT,
    last_run_at TEXT,
    last_status TEXT NOT NULL DEFAULT '',
    skipped INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Fresh-context reviews of a conversation's changes (ccc.reviews).
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    status TEXT NOT NULL DEFAULT 'pending',
    instructions TEXT NOT NULL DEFAULT '',
    until_pass INTEGER NOT NULL DEFAULT 0,
    round INTEGER NOT NULL DEFAULT 1,
    max_rounds INTEGER NOT NULL DEFAULT 1,
    loop_id INTEGER,
    scope TEXT NOT NULL DEFAULT '',
    base_run_id INTEGER NOT NULL DEFAULT 0,
    last_run_id INTEGER NOT NULL DEFAULT 0,
    verdict TEXT,
    report TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    fix_item_id INTEGER,
    not_before TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

-- Goal loops (ccc.goals): rounds of work, a check and a review each.
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    goal TEXT NOT NULL,
    check_command TEXT NOT NULL DEFAULT '',
    max_rounds INTEGER NOT NULL DEFAULT 5,
    round INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'running',
    phase TEXT NOT NULL DEFAULT 'coding',
    item_id INTEGER,
    review_id INTEGER,
    base_run_id INTEGER NOT NULL DEFAULT 0,
    last_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT
);

-- Multi-agent workflows (ccc.workflows): your templates, runs, step history.
CREATE TABLE IF NOT EXISTS workflow_templates (
    name TEXT PRIMARY KEY,
    definition TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    name TEXT NOT NULL,
    template TEXT NOT NULL,
    task TEXT NOT NULL,
    check_command TEXT NOT NULL DEFAULT '',
    max_rounds INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'running',
    step INTEGER NOT NULL DEFAULT 0,
    round INTEGER NOT NULL DEFAULT 1,
    base_run_id INTEGER NOT NULL DEFAULT 0,
    item_id INTEGER,
    plan TEXT NOT NULL DEFAULT '',
    findings TEXT NOT NULL DEFAULT '',
    last_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS workflow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES workflow_runs(id),
    step INTEGER NOT NULL,
    round INTEGER NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    verdict TEXT NOT NULL DEFAULT '',
    output TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permission_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    input_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS permission_allow (
    queue_id INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    PRIMARY KEY (queue_id, tool_name)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    queue_item_id INTEGER,
    prompt_text TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    cost_usd REAL NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    num_turns INTEGER NOT NULL DEFAULT 0,
    result_text TEXT,
    error_text TEXT,
    git_commit TEXT,
    git_note TEXT
);

CREATE TABLE IF NOT EXISTS run_model_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_run_id INTEGER NOT NULL REFERENCES prompt_runs(id),
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0
);

-- Name of a deleted conversation, so its token history (kept in
-- prompt_runs / run_model_usage / session_usage) still has a label.
CREATE TABLE IF NOT EXISTS deleted_queues (
    queue_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    project_path TEXT NOT NULL,
    deleted_at TEXT NOT NULL
);

-- Token usage of interactive (remote-control) sessions, read from their
-- transcripts by session_usage.UsageCollector: one row per model reply.
CREATE TABLE IF NOT EXISTS session_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    message_id TEXT NOT NULL,
    model TEXT NOT NULL,
    at TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    UNIQUE (queue_id, message_id)
);

-- The group a conversation was put into in the list (ccc.groups); none =
-- its project's. A table of its own so the migration is just this table:
-- an older ccc ignores it, DROP TABLE takes the database back.
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_sha256 TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    last_ip TEXT NOT NULL DEFAULT ''
);

-- What the API's devices did (ccc.api_audit): kept apart so an older ccc ignores it.
CREATE TABLE IF NOT EXISTS api_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    device_id INTEGER,
    device_name TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    status INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS conversation_groups (
    queue_id INTEGER PRIMARY KEY REFERENCES queues(id),
    name TEXT NOT NULL
);
"""


def _dump_flags(flags: list[str] | None) -> str | None:
    return json.dumps(flags) if flags is not None else None


def make_private(db_path: str | Path) -> None:
    """The database holds the devices' token fingerprints, the Telegram token and Discord webhook, and every
    conversation: only its owner may read it (#160). Also its journal files, when there are any."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.chmod(f"{db_path}{suffix}", 0o600)
        except OSError:
            pass  # not there (or not ours): nothing to tighten


class ArchivedConversationError(ValueError):
    """A task or message was meant for a conversation that sits in the Archive."""


class Storage:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()
            self._migrate_queues_unique_key(conn)
            self._migrate_add_always_resume_column(conn)
            self._migrate_api_token_to_device(conn)
            self._migrate_devices_history(conn)
            self._migrate_devices_streams(conn)
        make_private(self.db_path)


    def _migrate_devices_history(self, conn: sqlite3.Connection) -> None:
        """Signing a device out keeps its row (history) instead of deleting it.
        Only adds two columns, so an older ccc still runs on the same file
        (it would just list the signed-out rows too, with their dead tokens)."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(devices)")}
        if "revoked_at" not in columns:
            conn.execute("ALTER TABLE devices ADD COLUMN revoked_at TEXT")
            conn.execute("ALTER TABLE devices ADD COLUMN revoked_by TEXT NOT NULL DEFAULT ''")
            conn.commit()

    def _migrate_devices_streams(self, conn: sqlite3.Connection) -> None:
        """`streams`: the device has opened the events connection (GET /events); kept for information, nothing signs a device out by it."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(devices)")}
        if "streams" not in columns:
            conn.execute("ALTER TABLE devices ADD COLUMN streams INTEGER NOT NULL DEFAULT 0")
            conn.commit()

    def _migrate_api_token_to_device(self, conn: sqlite3.Connection) -> None:
        """The single API token of before devices existed becomes a device,
        so the phone that has it stays signed in."""
        row = conn.execute("SELECT value FROM settings WHERE key = 'api_token_sha256'").fetchone()
        if row is None:
            return
        conn.execute(
            "INSERT OR IGNORE INTO devices (name, token_sha256, created_at) VALUES (?, ?, ?)",
            ("Original token", row["value"], datetime.now().isoformat()),
        )
        conn.execute("DELETE FROM settings WHERE key = 'api_token_sha256'")
        conn.commit()

    def _migrate_queues_unique_key(self, conn: sqlite3.Connection) -> None:
        """One-time migration: older databases had UNIQUE on project_path
        (one queue per directory). Multiple live Claude Code sessions can
        share a cwd, so the real identity is session_id — move the UNIQUE
        constraint there. SQLite can't drop a column constraint in place,
        so this rebuilds the table (id values, and therefore every
        queue_items/prompt_runs foreign key, are preserved exactly)."""
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='queues'"
        ).fetchone()
        if row is None or "project_path TEXT NOT NULL UNIQUE" not in row[0]:
            return  # already migrated (or a brand-new DB, already correct)
        conn.executescript(
            """
            BEGIN;
            ALTER TABLE queues RENAME TO queues_old_migration;
            CREATE TABLE queues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                project_path TEXT NOT NULL,
                session_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'idle',
                next_retry_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO queues SELECT * FROM queues_old_migration;
            DROP TABLE queues_old_migration;
            COMMIT;
            """
        )

    def _migrate_add_always_resume_column(self, conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(queues)").fetchall()}
        if "always_resume" not in columns:
            conn.execute("ALTER TABLE queues ADD COLUMN always_resume INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        if "delivery_mode" not in columns:
            conn.execute("ALTER TABLE queues ADD COLUMN delivery_mode TEXT NOT NULL DEFAULT 'headless'")
            conn.commit()
        for column in ("launch_flags", "first_run_flags"):
            if column not in columns:
                conn.execute(f"ALTER TABLE queues ADD COLUMN {column} TEXT")
                conn.commit()
        if "remote_control_url" not in columns:
            conn.execute("ALTER TABLE queues ADD COLUMN remote_control_url TEXT")
            conn.commit()
        if "last_seen_at" not in columns:
            conn.execute("ALTER TABLE queues ADD COLUMN last_seen_at TEXT")
            # Existing conversations start as seen — otherwise every old one
            # would show the "new reply" marker after upgrading.
            conn.execute("UPDATE queues SET last_seen_at = ?", (datetime.now().isoformat(),))
            conn.commit()
        if "profile" not in columns:
            conn.execute("ALTER TABLE queues ADD COLUMN profile TEXT NOT NULL DEFAULT ''")
            conn.commit()
        for column, default in (("auto_continue", 1), ("paused", 0), ("archived", 0), ("claude_stopped", 0)):
            if column not in columns:
                conn.execute(f"ALTER TABLE queues ADD COLUMN {column} INTEGER NOT NULL DEFAULT {default}")
                conn.commit()
        run_columns = {row[1] for row in conn.execute("PRAGMA table_info(prompt_runs)").fetchall()}
        for column in ("git_commit", "git_note"):
            if column not in run_columns:
                conn.execute(f"ALTER TABLE prompt_runs ADD COLUMN {column} TEXT")
                conn.commit()
        if "review_id" not in run_columns:
            conn.execute("ALTER TABLE prompt_runs ADD COLUMN review_id INTEGER")
            conn.commit()
        # The revert feature is gone: drop its column (SQLite 3.35+; an older one keeps the unused column).
        if "reverted_by" in run_columns and sqlite3.sqlite_version_info >= (3, 35, 0):
            conn.execute("ALTER TABLE prompt_runs DROP COLUMN reverted_by")
            conn.commit()
        if "workflow_run_id" not in run_columns:
            conn.execute("ALTER TABLE prompt_runs ADD COLUMN workflow_run_id INTEGER")
            conn.commit()
        item_columns = {row[1] for row in conn.execute("PRAGMA table_info(queue_items)").fetchall()}
        if "not_before" not in item_columns:
            conn.execute("ALTER TABLE queue_items ADD COLUMN not_before TEXT")
            conn.commit()
        if "continue_on_retry" not in item_columns:
            conn.execute("ALTER TABLE queue_items ADD COLUMN continue_on_retry INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        # Which routine queued an item / started a conversation (ccc.routines).
        if "routine_id" not in item_columns:
            conn.execute("ALTER TABLE queue_items ADD COLUMN routine_id INTEGER")
            conn.commit()
        if "model" not in item_columns:
            conn.execute("ALTER TABLE queue_items ADD COLUMN model TEXT")
            conn.commit()
        review_columns = {row[1] for row in conn.execute("PRAGMA table_info(reviews)").fetchall()}
        for column, kind in (("goal_id", "INTEGER"), ("model", "TEXT")):
            if column not in review_columns:
                conn.execute(f"ALTER TABLE reviews ADD COLUMN {column} {kind}")
                conn.commit()
        if "routine_id" not in columns:
            conn.execute("ALTER TABLE queues ADD COLUMN routine_id INTEGER")
            conn.commit()
        if "live_busy" not in columns:
            conn.execute("ALTER TABLE queues ADD COLUMN live_busy INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE queues ADD COLUMN live_finished_at TEXT")
            conn.commit()
        if "context_percent" not in columns:
            conn.execute("ALTER TABLE queues ADD COLUMN context_percent REAL")
            conn.execute("ALTER TABLE queues ADD COLUMN context_tokens INTEGER")
            conn.execute("ALTER TABLE queues ADD COLUMN context_window INTEGER")
            conn.commit()
        if "context_model" not in columns:
            conn.execute("ALTER TABLE queues ADD COLUMN context_model TEXT")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        # timeout: wait for another process's write lock instead of raising
        # "database is locked" immediately — needed once the daemon and a
        # TUI instance can both be writing to the same file concurrently.
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        # WAL: readers (the TUI redrawing) no longer block on a writer and
        # vice versa. The mode is stored in the file, so this is a no-op once
        # set; if another process holds the lock right now, keep the old mode.
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        return conn

    def create_queue(
        self,
        name: str,
        project_path: str,
        session_id: str,
        always_resume: bool = False,
        delivery_mode: DeliveryMode = DeliveryMode.HEADLESS,
        launch_flags: list[str] | None = None,
        first_run_flags: list[str] | None = None,
        auto_continue: bool = True,
        routine_id: int | None = None,
        profile: str = "",
    ) -> Queue:
        now = datetime.now().isoformat()
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO queues (name, project_path, session_id, status, created_at, updated_at, "
                "always_resume, delivery_mode, launch_flags, first_run_flags, auto_continue, routine_id, profile) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    project_path,
                    session_id,
                    QueueStatus.IDLE.value,
                    now,
                    now,
                    int(always_resume),
                    delivery_mode.value,
                    _dump_flags(launch_flags),
                    _dump_flags(first_run_flags),
                    int(auto_continue),
                    routine_id,
                    profile,
                ),
            )
            conn.commit()
            queue_id = cur.lastrowid
        return self.get_queue(queue_id)

    def get_queue_by_path(self, project_path: str) -> Queue | None:
        """The single queue for a bare directory (the plain `ccc
        add <path>` flow). Multiple queues CAN share a project_path now
        (one per attached live session — see get_queue_by_session_id), so
        this returns the oldest one for that path; use
        get_queue_by_session_id for anything discovery-related."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM queues WHERE project_path = ? ORDER BY id LIMIT 1", (project_path,)
            ).fetchone()
            return self._row_to_queue(row) if row else None

    def get_queue_by_session_id(self, session_id: str) -> Queue | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM queues WHERE session_id = ?", (session_id,)).fetchone()
            return self._row_to_queue(row) if row else None

    def get_queue(self, queue_id: int) -> Queue:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM queues WHERE id = ?", (queue_id,)).fetchone()
            return self._row_to_queue(row)

    def rename_queue(self, queue_id: int, name: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE queues SET name = ?, updated_at = ? WHERE id = ?",
                         (name, datetime.now().isoformat(), queue_id))

    def list_queues(self) -> list[Queue]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM queues ORDER BY name").fetchall()
            return [self._row_to_queue(r) for r in rows]

    def set_queue_session(self, queue_id: int, session_id: str) -> None:
        """Adopt the session id Claude Code actually used (fork / from-PR
        first runs return a new one). Clears the one-shot first-run flags;
        later runs plain --resume it. Raises sqlite3.IntegrityError if the
        id already belongs to another queue."""
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE queues SET session_id = ?, first_run_flags = NULL, always_resume = 1, updated_at = ? "
                "WHERE id = ?",
                (session_id, datetime.now().isoformat(), queue_id),
            )
            conn.commit()

    def recent_project_paths(self, limit: int = 10) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT project_path, MAX(updated_at) AS last FROM queues "
                "GROUP BY project_path ORDER BY last DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [r["project_path"] for r in rows]

    def update_queue_status(
        self,
        queue_id: int,
        status: QueueStatus,
        next_retry_at: datetime | None = None,
        last_error: str | None = None,
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE queues SET status = ?, next_retry_at = ?, last_error = ?, updated_at = ? WHERE id = ?",
                (
                    status.value,
                    next_retry_at.isoformat() if next_retry_at else None,
                    last_error,
                    datetime.now().isoformat(),
                    queue_id,
                ),
            )
            conn.commit()

    def retry_queue(self, queue_id: int) -> bool:
        """r in the TUI: get a queue that stopped going again. Blocked on an
        error: the task that failed goes back to pending — it keeps its
        place, so it runs first — and is tried again. Paused at a usage
        limit: it just carries on. False if the queue was neither."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT status FROM queues WHERE id = ?", (queue_id,)).fetchone()
            if row is None or row["status"] not in (QueueStatus.BLOCKED_ERROR.value, QueueStatus.PAUSED_LIMIT.value):
                return False
            if row["status"] == QueueStatus.BLOCKED_ERROR.value:
                # The run that blocked it is the queue's latest one.
                last = conn.execute(
                    "SELECT queue_item_id, status FROM prompt_runs WHERE queue_id = ? ORDER BY id DESC LIMIT 1",
                    (queue_id,),
                ).fetchone()
                if last is not None and last["status"] == RunStatus.ERROR.value and last["queue_item_id"] is not None:
                    conn.execute(
                        "UPDATE queue_items SET status = ? WHERE id = ? AND status = ?",
                        (ItemStatus.PENDING.value, last["queue_item_id"], ItemStatus.FAILED.value),
                    )
            conn.execute(
                "UPDATE queues SET status = ?, next_retry_at = NULL, last_error = NULL, updated_at = ? WHERE id = ?",
                (QueueStatus.IDLE.value, datetime.now().isoformat(), queue_id),
            )
            conn.commit()
            return True

    def ensure_not_archived(self, queue_id: int) -> None:
        """Raise ArchivedConversationError for an archived conversation: it is
        frozen, so nothing may be added to it until it's restored."""
        queue = self.find_queue(queue_id)
        if queue is not None and queue.archived:
            raise ArchivedConversationError(
                _("'{name}' is archived — restore it from the Archive first.").format(name=queue.name))

    def add_queue_item(self, queue_id: int, prompt_text: str, not_before: datetime | None = None,
                       routine_id: int | None = None, model: str | None = None) -> QueueItem:
        with closing(self._connect()) as conn:
            max_pos = conn.execute(
                "SELECT COALESCE(MAX(position), -1) FROM queue_items WHERE queue_id = ?", (queue_id,)
            ).fetchone()[0]
            now = datetime.now().isoformat()
            cur = conn.execute(
                "INSERT INTO queue_items (queue_id, prompt_text, position, status, added_at, not_before, routine_id, "
                "model) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (queue_id, prompt_text, max_pos + 1, ItemStatus.PENDING.value, now,
                 not_before.isoformat() if not_before else None, routine_id, model),
            )
            conn.execute("UPDATE queues SET claude_stopped = 0 WHERE id = ?", (queue_id,))  # work for it: start again
            conn.commit()
            item_id = cur.lastrowid
            row = conn.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
            return self._row_to_item(row)

    def next_pending_item(self, queue_id: int) -> QueueItem | None:
        """Read-only peek at the next pending item (for display). Does not
        claim it — two callers can see the same item. Use
        claim_next_pending_item() to actually take ownership of one."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM queue_items WHERE queue_id = ? AND status = ? ORDER BY position LIMIT 1",
                (queue_id, ItemStatus.PENDING.value),
            ).fetchone()
            return self._row_to_item(row) if row else None

    def claim_next_pending_item(self, queue_id: int) -> QueueItem | None:
        """Atomically transition the oldest pending item to 'running' and
        return it. Safe to call from multiple processes against the same
        SQLite file at once — SQLite's write lock makes the UPDATE...WHERE
        a single atomic claim, so at most one caller ever gets a given item
        (everyone else's UPDATE matches zero rows and gets None back)."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "UPDATE queue_items SET status = ? "
                "WHERE id = ("
                "  SELECT id FROM queue_items WHERE queue_id = ? AND status = ? "
                "  ORDER BY position LIMIT 1"
                ") AND status = ? "
                "RETURNING *",
                (
                    ItemStatus.RUNNING.value,
                    queue_id,
                    ItemStatus.PENDING.value,
                    ItemStatus.PENDING.value,
                ),
            ).fetchone()
            conn.commit()
            return self._row_to_item(row) if row else None

    def count_pending_items(self, queue_id: int) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM queue_items WHERE queue_id = ? AND status = ?",
                (queue_id, ItemStatus.PENDING.value),
            ).fetchone()
            return row[0]

    def count_all_pending_items(self) -> int:
        """Pending items of all conversations that aren't archived (the menu's Tasks count)."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM queue_items i JOIN queues q ON q.id = i.queue_id "
                "WHERE i.status = ? AND NOT q.archived",
                (ItemStatus.PENDING.value,),
            ).fetchone()
            return row[0]

    def list_undelivered_items(self, queue_id: int) -> list[QueueItem]:
        """Pending items plus the one being delivered now (it waits for Claude Code to be free): all of them
        are a message the person wrote and Claude hasn't got yet."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM queue_items WHERE queue_id = ? AND status IN (?, ?) ORDER BY position",
                (queue_id, ItemStatus.PENDING.value, ItemStatus.RUNNING.value),
            ).fetchall()
            return [self._row_to_item(r) for r in rows]

    def list_pending_items(self, queue_id: int) -> list[QueueItem]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM queue_items WHERE queue_id = ? AND status = ? ORDER BY position",
                (queue_id, ItemStatus.PENDING.value),
            ).fetchall()
            return [self._row_to_item(r) for r in rows]

    def _set_queue_flag(self, queue_id: int, column: str, value: bool) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                f"UPDATE queues SET {column} = ?, updated_at = ? WHERE id = ?",
                (int(value), datetime.now().isoformat(), queue_id),
            )
            conn.commit()

    def set_queue_auto_continue(self, queue_id: int, value: bool) -> None:
        """Switch auto-continue; a conversation already waiting at a usage
        limit changes its wait accordingly (#243): turning it on makes a
        stopped one carry on at the reset, turning it off makes a waiting one
        stop and wait for the user."""
        self._set_queue_flag(queue_id, "auto_continue", value)
        queue = self.find_queue(queue_id)
        if queue is None or queue.next_retry_at is None:
            return
        if value and queue.status == QueueStatus.PAUSED_LIMIT:
            self.update_queue_status(queue_id, QueueStatus.WAITING_RETRY, next_retry_at=queue.next_retry_at)
        elif not value and queue.status == QueueStatus.WAITING_RETRY and queue.last_error is None:
            # (a wait for the user's usage reserve keeps its note and stays)
            self.update_queue_status(queue_id, QueueStatus.PAUSED_LIMIT, next_retry_at=queue.next_retry_at)

    def set_queue_paused(self, queue_id: int, value: bool) -> None:
        self._set_queue_flag(queue_id, "paused", value)

    def set_queue_archived(self, queue_id: int, value: bool) -> None:
        self._set_queue_flag(queue_id, "archived", value)

    def set_claude_stopped(self, queue_id: int, value: bool) -> None:
        """The user ended the conversation's Claude Code (True): the daemon
        leaves it stopped until it's opened or a message / task arrives."""
        self._set_queue_flag(queue_id, "claude_stopped", value)

    def set_remote_control_url(self, queue_id: int, url: str | None) -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE queues SET remote_control_url = ? WHERE id = ?", (url, queue_id))
            conn.commit()

    def add_queue_item_front(self, queue_id: int, prompt_text: str) -> QueueItem:
        """Queue a prompt ahead of everything pending (e.g. "continue" after
        a usage limit interrupted the current task)."""
        with closing(self._connect()) as conn:
            min_pos = conn.execute(
                "SELECT COALESCE(MIN(position), 0) FROM queue_items WHERE queue_id = ?", (queue_id,)
            ).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO queue_items (queue_id, prompt_text, position, status, added_at) VALUES (?, ?, ?, ?, ?)",
                (queue_id, prompt_text, min_pos - 1, ItemStatus.PENDING.value, datetime.now().isoformat()),
            )
            conn.execute("UPDATE queues SET claude_stopped = 0 WHERE id = ?", (queue_id,))
            conn.commit()
            row = conn.execute("SELECT * FROM queue_items WHERE id = ?", (cur.lastrowid,)).fetchone()
            return self._row_to_item(row)

    def find_queue(self, queue_id: int) -> Queue | None:
        """Like get_queue, but None for a queue that no longer exists."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM queues WHERE id = ?", (queue_id,)).fetchone()
            return self._row_to_queue(row) if row else None

    def delete_queue(self, queue_id: int) -> None:
        """Remove a conversation: its queue, items and approvals, and the
        texts of its runs. The token numbers stay (runs, per-model and
        remote-control usage) under its old name in deleted_queues, so the
        usage history doesn't change when a conversation is deleted.
        Claude Code's own transcript is left alone."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT name, project_path FROM queues WHERE id = ?", (queue_id,)).fetchone()
            if row is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO deleted_queues (queue_id, name, project_path, deleted_at) VALUES (?, ?, ?, ?)",
                    (queue_id, row["name"], row["project_path"], datetime.now().isoformat()),
                )
            conn.execute(
                "UPDATE prompt_runs SET prompt_text = '', result_text = NULL, error_text = NULL WHERE queue_id = ?",
                (queue_id,),
            )
            conn.execute("DELETE FROM queue_items WHERE queue_id = ?", (queue_id,))
            conn.execute("DELETE FROM routines WHERE queue_id = ?", (queue_id,))
            conn.execute("DELETE FROM reviews WHERE queue_id = ?", (queue_id,))
            conn.execute("DELETE FROM goals WHERE queue_id = ?", (queue_id,))
            conn.execute("DELETE FROM workflow_events WHERE run_id IN (SELECT id FROM workflow_runs WHERE queue_id = ?)",
                         (queue_id,))
            conn.execute("DELETE FROM workflow_runs WHERE queue_id = ?", (queue_id,))
            conn.execute("DELETE FROM permission_requests WHERE queue_id = ?", (queue_id,))
            conn.execute("DELETE FROM permission_allow WHERE queue_id = ?", (queue_id,))
            conn.execute("DELETE FROM conversation_groups WHERE queue_id = ?", (queue_id,))
            conn.execute("DELETE FROM queues WHERE id = ?", (queue_id,))
            conn.commit()

    # --- groups in the list (ccc.groups) -----------------------------------------

    def conversation_groups(self) -> dict[int, str]:
        with closing(self._connect()) as conn:
            return {row["queue_id"]: row["name"]
                    for row in conn.execute("SELECT queue_id, name FROM conversation_groups").fetchall()}

    def set_conversation_group(self, queue_id: int, name: str | None) -> None:
        """Put a conversation into a group; None / "" takes it out."""
        with closing(self._connect()) as conn:
            if name:
                conn.execute(
                    "INSERT INTO conversation_groups (queue_id, name) VALUES (?, ?) "
                    "ON CONFLICT(queue_id) DO UPDATE SET name = excluded.name",
                    (queue_id, name),
                )
            else:
                conn.execute("DELETE FROM conversation_groups WHERE queue_id = ?", (queue_id,))
            conn.commit()

    def rename_conversation_group(self, old: str, new: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE conversation_groups SET name = ? WHERE name = ?", (new, old))
            conn.commit()

    def delete_conversation_group(self, name: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM conversation_groups WHERE name = ?", (name,))
            conn.commit()

    # --- routines (ccc.routines) -----------------------------------------------

    def add_routine(self, queue_id: int, name: str, prompt_text: str, schedule_kind: str, schedule_value: str,
                    cron: str, target: RoutineTarget, next_run_at: datetime | None) -> Routine:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO routines (queue_id, name, prompt_text, schedule_kind, schedule_value, cron, target, "
                "next_run_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (queue_id, name, prompt_text, schedule_kind, schedule_value, cron, target.value,
                 next_run_at.isoformat() if next_run_at else None, datetime.now().isoformat()),
            )
            conn.commit()
            routine_id = cur.lastrowid
        return self.get_routine(routine_id)

    def find_routine(self, routine_id: int) -> Routine | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()
            return self._row_to_routine(row) if row else None

    def get_routine(self, routine_id: int) -> Routine:
        routine = self.find_routine(routine_id)
        if routine is None:
            raise KeyError(routine_id)
        return routine

    def list_routines(self) -> list[Routine]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM routines ORDER BY id").fetchall()
            return [self._row_to_routine(r) for r in rows]

    def update_routine(self, routine_id: int, **fields) -> None:
        """Set columns of a routine (datetimes and enums are stored as text)."""
        if not fields:
            return
        values = []
        for value in fields.values():
            if isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, Enum):
                value = value.value
            elif isinstance(value, bool):
                value = int(value)
            values.append(value)
        columns = ", ".join(f"{name} = ?" for name in fields)
        with closing(self._connect()) as conn:
            conn.execute(f"UPDATE routines SET {columns} WHERE id = ?", (*values, routine_id))
            conn.commit()

    def delete_routine(self, routine_id: int) -> None:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
            conn.commit()

    def routine_items_active(self, routine_id: int) -> int:
        """Tasks a routine queued that haven't finished (pending or running)."""
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM queue_items WHERE routine_id = ? AND status IN (?, ?)",
                (routine_id, ItemStatus.PENDING.value, ItemStatus.RUNNING.value),
            ).fetchone()[0]

    def routine_queues(self, routine_id: int | None = None, archived: bool | None = None) -> list[Queue]:
        """Conversations routines started (one routine's, or every one)."""
        sql, args = "SELECT * FROM queues WHERE routine_id IS NOT NULL", []
        if routine_id is not None:
            sql, args = sql + " AND routine_id = ?", [*args, routine_id]
        if archived is not None:
            sql, args = sql + " AND archived = ?", [*args, int(archived)]
        with closing(self._connect()) as conn:
            return [self._row_to_queue(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]

    @staticmethod
    def _row_to_routine(row: sqlite3.Row) -> Routine:
        def when(column: str) -> datetime | None:
            return datetime.fromisoformat(row[column]) if row[column] else None

        return Routine(
            id=row["id"], queue_id=row["queue_id"], name=row["name"], prompt_text=row["prompt_text"],
            schedule_kind=row["schedule_kind"], schedule_value=row["schedule_value"], cron=row["cron"],
            target=RoutineTarget(row["target"]), enabled=bool(row["enabled"]), next_run_at=when("next_run_at"),
            last_run_at=when("last_run_at"), last_status=row["last_status"], skipped=row["skipped"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # --- reviews (ccc.reviews) -------------------------------------------------

    def add_review(self, queue_id: int, instructions: str = "", until_pass: bool = False, round: int = 1,
                   max_rounds: int = 1, loop_id: int | None = None, goal_id: int | None = None,
                   model: str | None = None, base_run_id: int = 0) -> Review:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO reviews (queue_id, instructions, until_pass, round, max_rounds, loop_id, goal_id, model, "
                "base_run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (queue_id, instructions, int(until_pass), round, max_rounds, loop_id, goal_id, model, base_run_id,
                 datetime.now().isoformat()),
            )
            conn.commit()
            review_id = cur.lastrowid
        return self.get_review(review_id)

    def find_review(self, review_id: int) -> Review | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
            return self._row_to_review(row) if row else None

    def get_review(self, review_id: int) -> Review:
        review = self.find_review(review_id)
        if review is None:
            raise KeyError(review_id)
        return review

    def list_reviews(self, queue_id: int | None = None, status: ReviewStatus | None = None) -> list[Review]:
        """Newest first."""
        sql, args = "SELECT * FROM reviews WHERE 1 = 1", []
        if queue_id is not None:
            sql, args = sql + " AND queue_id = ?", [*args, queue_id]
        if status is not None:
            sql, args = sql + " AND status = ?", [*args, status.value]
        with closing(self._connect()) as conn:
            return [self._row_to_review(r) for r in conn.execute(sql + " ORDER BY id DESC", args).fetchall()]

    def update_review(self, review_id: int, **fields) -> None:
        if not fields:
            return
        values = []
        for value in fields.values():
            if isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, Enum):
                value = value.value
            elif isinstance(value, bool):
                value = int(value)
            values.append(value)
        columns = ", ".join(f"{name} = ?" for name in fields)
        with closing(self._connect()) as conn:
            conn.execute(f"UPDATE reviews SET {columns} WHERE id = ?", (*values, review_id))
            conn.commit()

    def task_runs_after(self, queue_id: int, run_id: int) -> list[PromptRun]:
        """The conversation's task runs (not reviews) newer than run_id, oldest first."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM prompt_runs WHERE queue_id = ? AND id > ? AND review_id IS NULL "
                "AND workflow_run_id IS NULL ORDER BY id",
                (queue_id, run_id),
            ).fetchall()
            return [self._row_to_run(r) for r in rows]

    def last_run_id(self, queue_id: int) -> int:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM prompt_runs WHERE queue_id = ? AND review_id IS NULL "
                "AND workflow_run_id IS NULL", (queue_id,)
            ).fetchone()[0]

    def get_item(self, item_id: int) -> QueueItem | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
            return self._row_to_item(row) if row else None

    @staticmethod
    def _row_to_review(row: sqlite3.Row) -> Review:
        def when(column: str) -> datetime | None:
            return datetime.fromisoformat(row[column]) if row[column] else None

        return Review(
            id=row["id"], queue_id=row["queue_id"], status=ReviewStatus(row["status"]),
            instructions=row["instructions"], until_pass=bool(row["until_pass"]), round=row["round"],
            max_rounds=row["max_rounds"], loop_id=row["loop_id"], scope=row["scope"],
            base_run_id=row["base_run_id"], last_run_id=row["last_run_id"], verdict=Verdict(row["verdict"]) if row["verdict"] else None,
            report=row["report"], error=row["error"], fix_item_id=row["fix_item_id"],
            not_before=when("not_before"), created_at=datetime.fromisoformat(row["created_at"]),
            finished_at=when("finished_at"), goal_id=row["goal_id"], model=row["model"],
        )

    # --- goals (ccc.goals) -------------------------------------------------------

    def add_goal(self, queue_id: int, goal: str, check_command: str, max_rounds: int, base_run_id: int) -> Goal:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO goals (queue_id, goal, check_command, max_rounds, base_run_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (queue_id, goal, check_command, max_rounds, base_run_id, datetime.now().isoformat()),
            )
            conn.commit()
            goal_id = cur.lastrowid
        return self.get_goal(goal_id)

    def find_goal(self, goal_id: int) -> Goal | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
            return self._row_to_goal(row) if row else None

    def get_goal(self, goal_id: int) -> Goal:
        goal = self.find_goal(goal_id)
        if goal is None:
            raise KeyError(goal_id)
        return goal

    def list_goals(self, queue_id: int | None = None, status: GoalStatus | None = None) -> list[Goal]:
        """Newest first."""
        sql, args = "SELECT * FROM goals WHERE 1 = 1", []
        if queue_id is not None:
            sql, args = sql + " AND queue_id = ?", [*args, queue_id]
        if status is not None:
            sql, args = sql + " AND status = ?", [*args, status.value]
        with closing(self._connect()) as conn:
            return [self._row_to_goal(r) for r in conn.execute(sql + " ORDER BY id DESC", args).fetchall()]

    def latest_goals(self) -> dict[int, Goal]:
        """The newest goal of every conversation that has one, in one query (the Dashboard and the API list)."""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM goals WHERE id IN (SELECT MAX(id) FROM goals GROUP BY queue_id)").fetchall()
        return {r["queue_id"]: self._row_to_goal(r) for r in rows}

    def update_goal(self, goal_id: int, **fields) -> None:
        if not fields:
            return
        values = []
        for value in fields.values():
            if isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, Enum):
                value = value.value
            values.append(value)
        columns = ", ".join(f"{name} = ?" for name in fields)
        with closing(self._connect()) as conn:
            conn.execute(f"UPDATE goals SET {columns} WHERE id = ?", (*values, goal_id))
            conn.commit()

    @staticmethod
    def _row_to_goal(row: sqlite3.Row) -> Goal:
        return Goal(
            id=row["id"], queue_id=row["queue_id"], goal=row["goal"], check_command=row["check_command"],
            max_rounds=row["max_rounds"], round=row["round"], status=GoalStatus(row["status"]),
            phase=GoalPhase(row["phase"]), item_id=row["item_id"], review_id=row["review_id"],
            base_run_id=row["base_run_id"], last_reason=row["last_reason"],
            created_at=datetime.fromisoformat(row["created_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        )

    # --- workflows (ccc.workflows) -----------------------------------------------

    def save_workflow_template(self, name: str, definition: dict) -> None:
        with closing(self._connect()) as conn:
            conn.execute("INSERT INTO workflow_templates (name, definition) VALUES (?, ?) "
                         "ON CONFLICT(name) DO UPDATE SET definition = excluded.definition",
                         (name, json.dumps(definition, ensure_ascii=False)))
            conn.commit()

    def workflow_templates(self) -> dict[str, dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT name, definition FROM workflow_templates ORDER BY name").fetchall()
            return {r["name"]: json.loads(r["definition"]) for r in rows}

    def delete_workflow_template(self, name: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM workflow_templates WHERE name = ?", (name,))
            conn.commit()

    def add_workflow_run(self, queue_id: int, name: str, template: dict, task: str, check_command: str,
                         max_rounds: int, base_run_id: int) -> WorkflowRun:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO workflow_runs (queue_id, name, template, task, check_command, max_rounds, base_run_id, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (queue_id, name, json.dumps(template, ensure_ascii=False), task, check_command, max_rounds,
                 base_run_id, datetime.now().isoformat()),
            )
            conn.commit()
            run_id = cur.lastrowid
        return self.get_workflow_run(run_id)

    def find_workflow_run(self, run_id: int) -> WorkflowRun | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
            return self._row_to_workflow_run(row) if row else None

    def get_workflow_run(self, run_id: int) -> WorkflowRun:
        run = self.find_workflow_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def list_workflow_runs(self, queue_id: int | None = None) -> list[WorkflowRun]:
        """Newest first."""
        sql, args = "SELECT * FROM workflow_runs", []
        if queue_id is not None:
            sql, args = sql + " WHERE queue_id = ?", [queue_id]
        with closing(self._connect()) as conn:
            return [self._row_to_workflow_run(r) for r in conn.execute(sql + " ORDER BY id DESC", args).fetchall()]

    def update_workflow_run(self, run_id: int, **fields) -> None:
        if not fields:
            return
        values = []
        for value in fields.values():
            if isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, Enum):
                value = value.value
            values.append(value)
        columns = ", ".join(f"{name} = ?" for name in fields)
        with closing(self._connect()) as conn:
            conn.execute(f"UPDATE workflow_runs SET {columns} WHERE id = ?", (*values, run_id))
            conn.commit()

    def add_workflow_event(self, run_id: int, step: int, round: int, kind: str, name: str, status: str = "running",
                           verdict: str = "", output: str = "") -> int:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO workflow_events (run_id, step, round, kind, name, status, verdict, output, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, step, round, kind, name, status, verdict, output, datetime.now().isoformat()),
            )
            conn.commit()
            return cur.lastrowid

    def update_workflow_event(self, event_id: int, status: str, verdict: str = "", output: str = "") -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE workflow_events SET status = ?, verdict = ?, output = ? WHERE id = ?",
                         (status, verdict, output, event_id))
            conn.commit()

    def workflow_events(self, run_id: int) -> list[WorkflowEvent]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM workflow_events WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
            return [WorkflowEvent(id=r["id"], run_id=r["run_id"], step=r["step"], round=r["round"], kind=r["kind"],
                                  name=r["name"], status=r["status"], verdict=r["verdict"], output=r["output"],
                                  created_at=datetime.fromisoformat(r["created_at"])) for r in rows]

    @staticmethod
    def _row_to_workflow_run(row: sqlite3.Row) -> WorkflowRun:
        return WorkflowRun(
            id=row["id"], queue_id=row["queue_id"], name=row["name"], template=json.loads(row["template"]),
            task=row["task"], check_command=row["check_command"], max_rounds=row["max_rounds"],
            status=WorkflowStatus(row["status"]), step=row["step"], round=row["round"],
            base_run_id=row["base_run_id"], item_id=row["item_id"], plan=row["plan"], findings=row["findings"],
            last_reason=row["last_reason"], created_at=datetime.fromisoformat(row["created_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        )

    def run_for_item(self, item_id: int) -> PromptRun | None:
        """The newest run of a queue item."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM prompt_runs WHERE queue_item_id = ? ORDER BY id DESC LIMIT 1",
                               (item_id,)).fetchone()
            return self._row_to_run(row) if row else None

    # --- tool permission requests -------------------------------------------

    @staticmethod
    def _row_to_request(row: sqlite3.Row) -> PermissionRequest:
        return PermissionRequest(
            id=row["id"],
            queue_id=row["queue_id"],
            tool_name=row["tool_name"],
            input=json.loads(row["input_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            status=row["status"],
        )

    def create_permission_request(self, queue_id: int, tool_name: str, tool_input: dict) -> PermissionRequest:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO permission_requests (queue_id, tool_name, input_json, created_at) VALUES (?, ?, ?, ?)",
                (queue_id, tool_name, json.dumps(tool_input, ensure_ascii=False), datetime.now().isoformat()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM permission_requests WHERE id = ?", (cur.lastrowid,)).fetchone()
            return self._row_to_request(row)

    def get_permission_request(self, request_id: int) -> PermissionRequest | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM permission_requests WHERE id = ?", (request_id,)).fetchone()
            return self._row_to_request(row) if row else None

    def pending_permission_requests(self, queue_id: int) -> list[PermissionRequest]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM permission_requests WHERE queue_id = ? AND status = 'pending' ORDER BY id",
                (queue_id,),
            ).fetchall()
            return [self._row_to_request(r) for r in rows]

    def queues_waiting_for_approval(self) -> set[int]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT DISTINCT queue_id FROM permission_requests WHERE status = 'pending'").fetchall()
            return {r[0] for r in rows}

    def decide_permission_request(self, request_id: int, allow: bool) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE permission_requests SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
                ("allowed" if allow else "denied", datetime.now().isoformat(), request_id),
            )
            conn.commit()

    def always_allow_tool(self, queue_id: int, tool_name: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO permission_allow (queue_id, tool_name) VALUES (?, ?)", (queue_id, tool_name)
            )
            conn.commit()

    def always_allowed_tools(self, queue_id: int) -> set[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT tool_name FROM permission_allow WHERE queue_id = ?", (queue_id,)).fetchall()
            return {r["tool_name"] for r in rows}

    def is_tool_always_allowed(self, queue_id: int, tool_name: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM permission_allow WHERE queue_id = ? AND tool_name = ?", (queue_id, tool_name)
            ).fetchone()
            return row is not None

    def mark_seen(self, queue_id: int) -> None:
        # Not updated_at: viewing isn't activity (recent_project_paths).
        with closing(self._connect()) as conn:
            conn.execute("UPDATE queues SET last_seen_at = ? WHERE id = ?", (datetime.now().isoformat(), queue_id))
            conn.commit()

    def set_live_busy(self, queue_id: int, busy: bool) -> None:
        """What an interactive conversation's Claude Code is doing right now
        (typed into its pane, not a queued prompt). Going from busy to idle
        is a finished piece of work: unseen_queue_ids() reports it."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT live_busy FROM queues WHERE id = ?", (queue_id,)).fetchone()
            if row is None or bool(row[0]) == busy:
                return
            if busy:
                conn.execute("UPDATE queues SET live_busy = 1 WHERE id = ?", (queue_id,))
            else:
                conn.execute("UPDATE queues SET live_busy = 0, live_finished_at = ? WHERE id = ?",
                             (datetime.now().isoformat(), queue_id))
            conn.commit()

    def set_context(self, session_id: str, percent: float, tokens: int | None, window: int | None,
                    model: str | None = None) -> bool:
        """How full the context window of the conversation with this session
        is, and which model works on it (reported by Claude Code's status
        line). False when no conversation has the session."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "UPDATE queues SET context_percent = ?, context_tokens = ?, context_window = ?, "
                "context_model = COALESCE(?, context_model) WHERE session_id = ?",
                (percent, tokens, window, model, session_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def unseen_queue_ids(self) -> set[int]:
        """Queues with a run (or live work in their Claude Code) that
        finished after they were last viewed."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT DISTINCT q.id FROM queues q JOIN prompt_runs r ON r.queue_id = q.id "
                "WHERE r.finished_at IS NOT NULL AND (q.last_seen_at IS NULL OR r.finished_at > q.last_seen_at)"
            ).fetchall()
            live = conn.execute(
                "SELECT id FROM queues WHERE live_finished_at IS NOT NULL "
                "AND (last_seen_at IS NULL OR live_finished_at > last_seen_at)"
            ).fetchall()
            return {r[0] for r in rows} | {r[0] for r in live}

    def set_item_continue_on_retry(self, item_id: int, value: bool) -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE queue_items SET continue_on_retry = ? WHERE id = ?", (int(value), item_id))
            conn.commit()

    def update_item_schedule(self, item_id: int, not_before: datetime | None) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE queue_items SET not_before = ? WHERE id = ?",
                (not_before.isoformat() if not_before else None, item_id),
            )
            conn.commit()

    def update_item_text(self, item_id: int, text: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE queue_items SET prompt_text = ? WHERE id = ?", (text, item_id))
            conn.commit()

    def skip_item(self, item_id: int) -> None:
        self.set_item_status(item_id, ItemStatus.SKIPPED)

    def move_item(self, item_id: int, delta: int) -> bool:
        """Swap a pending item with its pending neighbour (delta -1 = up,
        +1 = down). Returns False when it's already at that edge."""
        with closing(self._connect()) as conn:
            item = conn.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
            if item is None:
                return False
            cmp, order = ("<", "DESC") if delta < 0 else (">", "ASC")
            other = conn.execute(
                f"SELECT * FROM queue_items WHERE queue_id = ? AND status = ? AND position {cmp} ? "
                f"ORDER BY position {order} LIMIT 1",
                (item["queue_id"], ItemStatus.PENDING.value, item["position"]),
            ).fetchone()
            if other is None:
                return False
            conn.execute("UPDATE queue_items SET position = ? WHERE id = ?", (other["position"], item["id"]))
            conn.execute("UPDATE queue_items SET position = ? WHERE id = ?", (item["position"], other["id"]))
            conn.commit()
            return True

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()

    def delete_setting_if(self, key: str, value: str) -> bool:
        """Delete a setting only while it still has this value; True when this call did.
        Of two processes trying at once, exactly one gets True."""
        with closing(self._connect()) as conn:
            deleted = conn.execute("DELETE FROM settings WHERE key = ? AND value = ?", (key, value)).rowcount
            conn.commit()
        return bool(deleted)

    @staticmethod
    def _row_to_device(row: sqlite3.Row) -> Device:
        return Device(
            id=row["id"], name=row["name"], token_sha256=row["token_sha256"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]) if row["last_seen_at"] else None,
            last_ip=row["last_ip"],
            revoked_at=datetime.fromisoformat(row["revoked_at"]) if row["revoked_at"] else None,
            revoked_by=row["revoked_by"] or "",
        )

    def add_device(self, name: str, token_sha256: str) -> Device:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO devices (name, token_sha256, created_at) VALUES (?, ?, ?)",
                (name, token_sha256, datetime.now().isoformat()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM devices WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._row_to_device(row)

    def list_devices(self, include_revoked: bool = False) -> list[Device]:
        """The devices signed in; with include_revoked also the signed-out ones (history)."""
        where = "" if include_revoked else "WHERE revoked_at IS NULL"
        with closing(self._connect()) as conn:
            rows = conn.execute(f"SELECT * FROM devices {where} ORDER BY id").fetchall()
        return [self._row_to_device(row) for row in rows]

    def device_by_token(self, token_sha256: str) -> Device | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM devices WHERE token_sha256 = ? AND revoked_at IS NULL", (token_sha256,)).fetchone()
        return self._row_to_device(row) if row else None

    def device_by_id(self, device_id: int) -> Device | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        return self._row_to_device(row) if row else None

    def touch_device(self, device_id: int, ip: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE devices SET last_seen_at = ?, last_ip = ? WHERE id = ?",
                         (datetime.now().isoformat(), ip, device_id))
            conn.commit()

    def rename_device(self, device_id: int, name: str) -> bool:
        with closing(self._connect()) as conn:
            changed = conn.execute("UPDATE devices SET name = ? WHERE id = ? AND revoked_at IS NULL", (name, device_id)).rowcount
            conn.commit()
        return bool(changed)

    AUDIT_KEEP = 5000  # entries of known devices
    AUDIT_KEEP_STRANGERS = 1000  # entries without a device (wrong tokens, pairing): a scan must not push the rest out

    def add_audit(self, device_id: int | None, device_name: str, ip: str, method: str, path: str, status: int,
                  detail: str = "") -> None:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO api_audit (at, device_id, device_name, ip, method, path, status, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(), device_id, device_name, ip, method, path, status, detail))
            if device_id is None:
                keep, where = self.AUDIT_KEEP_STRANGERS, "device_id IS NULL"
            else:
                keep, where = self.AUDIT_KEEP, "device_id IS NOT NULL"
            conn.execute(f"DELETE FROM api_audit WHERE {where} AND id NOT IN "
                         f"(SELECT id FROM api_audit WHERE {where} ORDER BY id DESC LIMIT ?)", (keep,))
            conn.commit()

    def list_audit(self, limit: int = 100) -> list[dict]:
        """The newest entries first."""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM api_audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def revoke_device(self, device_id: int, by: str) -> bool:
        """Sign an active device out: the row stays (when and how: `by`), the
        token stops working and is overwritten. False when there is no such active device."""
        with closing(self._connect()) as conn:
            changed = conn.execute(
                "UPDATE devices SET revoked_at = ?, revoked_by = ?, token_sha256 = 'revoked-' || id "
                "WHERE id = ? AND revoked_at IS NULL", (datetime.now().isoformat(), by, device_id)).rowcount
            conn.commit()
        return bool(changed)

    def revoke_inactive_devices(self, days: int, by: str) -> list[int]:
        """Sign out the devices not seen for `days` (a never-seen one counts from its pairing): an uninstalled
        app cannot tell the server, so silence is the only sign. Returns their ids; the rows stay as history."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with closing(self._connect()) as conn:
            ids = [row["id"] for row in conn.execute(
                "SELECT id FROM devices WHERE revoked_at IS NULL AND COALESCE(last_seen_at, created_at) < ?", (cutoff,))]
            for device_id in ids:
                conn.execute("UPDATE devices SET revoked_at = ?, revoked_by = ?, token_sha256 = 'revoked-' || id "
                             "WHERE id = ?", (datetime.now().isoformat(), by, device_id))
            conn.commit()
        return ids

    def mark_streaming(self, device_id: int, ip: str) -> None:
        """The device opened the events connection: it is in touch right now (last seen, address)."""
        with closing(self._connect()) as conn:
            conn.execute("UPDATE devices SET streams = 1, last_seen_at = ?, last_ip = ? WHERE id = ?",
                         (datetime.now().isoformat(), ip, device_id))
            conn.commit()

    def revoke_all_devices(self, by: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE devices SET revoked_at = ?, revoked_by = ?, token_sha256 = 'revoked-' || id "
                "WHERE revoked_at IS NULL", (datetime.now().isoformat(), by))
            conn.commit()

    def set_item_status(self, item_id: int, status: ItemStatus) -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE queue_items SET status = ? WHERE id = ?", (status.value, item_id))
            conn.commit()

    def reset_running_items_to_pending(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE queue_items SET status = ? WHERE status = ?",
                (ItemStatus.PENDING.value, ItemStatus.RUNNING.value),
            )
            conn.execute(
                "UPDATE queues SET status = ? WHERE status = ?",
                (QueueStatus.IDLE.value, QueueStatus.RUNNING.value),
            )
            conn.commit()

    def insert_prompt_run(self, run: PromptRun) -> int:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO prompt_runs (queue_id, queue_item_id, prompt_text, started_at, finished_at, "
                "status, cost_usd, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
                "duration_ms, num_turns, result_text, error_text, git_commit, git_note, review_id, workflow_run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.queue_id,
                    run.queue_item_id,
                    run.prompt_text,
                    run.started_at.isoformat(),
                    run.finished_at.isoformat() if run.finished_at else None,
                    run.status.value,
                    run.cost_usd,
                    run.input_tokens,
                    run.output_tokens,
                    run.cache_read_tokens,
                    run.cache_creation_tokens,
                    run.duration_ms,
                    run.num_turns,
                    run.result_text,
                    run.error_text,
                    run.git_commit,
                    run.git_note,
                    run.review_id,
                    run.workflow_run_id,
                ),
            )
            conn.commit()
            return cur.lastrowid

    def insert_run_model_usage(
        self,
        prompt_run_id: int,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO run_model_usage (prompt_run_id, model, input_tokens, output_tokens, "
                "cache_read_tokens, cache_creation_tokens) VALUES (?, ?, ?, ?, ?, ?)",
                (prompt_run_id, model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens),
            )
            conn.commit()

    def total_tokens_by_model(self) -> list[tuple[str, int]]:
        """All-time total tokens (input+output+cache_read+cache_creation)
        per model actually used, across every run ccc has
        executed — descending by volume. This is ccc's own
        recorded history, not an account-wide limit from Anthropic (which
        doesn't expose a per-model breakdown on every plan)."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT model, SUM(t) AS total FROM ("
                " SELECT model, input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens AS t"
                " FROM run_model_usage UNION ALL"
                " SELECT model, input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens"
                " FROM session_usage"
                ") GROUP BY model ORDER BY total DESC"
            ).fetchall()
            return [(r[0], r[1] or 0) for r in rows]

    def run_summary_since(self, since: datetime) -> dict:
        """Tasks that finished since `since`: how many, in how many conversations, how many succeeded, the cost."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS runs, COUNT(DISTINCT queue_id) AS queues, "
                "COALESCE(SUM(status = 'success'), 0) AS success, COALESCE(SUM(cost_usd), 0) AS cost "
                "FROM prompt_runs WHERE finished_at >= ?", (since.isoformat(),)).fetchone()
        return {"runs": row["runs"], "queues": row["queues"], "success": row["success"], "cost": row["cost"]}

    def recent_runs(self, limit: int = 20) -> list[PromptRun]:
        """The latest runs of every conversation, newest first (the Dashboard's activity)."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM prompt_runs ORDER BY COALESCE(finished_at, started_at) DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_run(r) for r in rows]

    def pending_permission_requests_all(self) -> list[PermissionRequest]:
        """Every tool call that waits for your approval, oldest first."""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM permission_requests WHERE status = 'pending' ORDER BY id").fetchall()
            return [self._row_to_request(r) for r in rows]

    def runs_for_queue(self, queue_id: int) -> list[PromptRun]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM prompt_runs WHERE queue_id = ? ORDER BY started_at DESC", (queue_id,)
            ).fetchall()
            return [self._row_to_run(r) for r in rows]

    def failed_runs_for_queue(self, queue_id: int, limit: int = 5) -> list[PromptRun]:
        """The latest runs that did not succeed (error or rate limit), newest first."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM prompt_runs WHERE queue_id = ? AND status != ? ORDER BY started_at DESC LIMIT ?",
                (queue_id, RunStatus.SUCCESS.value, limit),
            ).fetchall()
            return [self._row_to_run(r) for r in rows]

    def failed_run_count(self, queue_id: int, since: str) -> int:
        """How many runs since `since` (ISO time) did not succeed."""
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM prompt_runs WHERE queue_id = ? AND status != ? AND started_at >= ?",
                (queue_id, RunStatus.SUCCESS.value, since),
            ).fetchone()[0]

    def daily_cost_totals(self, days: int = 30) -> list[tuple[str, float]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT substr(started_at, 1, 10) AS day, SUM(cost_usd) FROM prompt_runs "
                "WHERE started_at >= date('now', ?) GROUP BY day ORDER BY day",
                (f"-{days} days",),
            ).fetchall()
            return [(r[0], r[1]) for r in rows]

    def daily_token_totals(self, days: int = 30) -> list[tuple[str, int]]:
        """Total tokens (input+output+cache_read+cache_creation) per day —
        a more honest usage indicator than cost_usd for a subscription
        plan, since it reflects the same pool the account's usage limit
        actually draws from, not a notional per-token dollar price."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT day, SUM(t) FROM ("
                " SELECT substr(started_at, 1, 10) AS day,"
                " input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens AS t"
                " FROM prompt_runs WHERE started_at >= date('now', ?) UNION ALL"
                " SELECT substr(at, 1, 10),"
                " input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens"
                " FROM session_usage WHERE at >= date('now', ?)"
                ") GROUP BY day ORDER BY day",
                (f"-{days} days", f"-{days} days"),
            ).fetchall()
            return [(r[0], r[1] or 0) for r in rows]

    def insert_session_usage(self, queue_id: int, records) -> int:
        """Store model replies of an interactive session (see
        session_usage); a reply already stored is ignored. Returns how many
        were new."""
        with closing(self._connect()) as conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO session_usage (queue_id, message_id, model, at, input_tokens, output_tokens, "
                "cache_read_tokens, cache_creation_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (queue_id, r.message_id, r.model, r.at.isoformat(), r.input_tokens, r.output_tokens,
                     r.cache_read_tokens, r.cache_creation_tokens)
                    for r in records
                ],
            )
            conn.commit()
            return conn.total_changes - before

    def queue_token_total(self, queue_id: int, day=None) -> int:
        """All tokens a conversation used — headless runs plus replies of its
        remote-control session — optionally only on one day."""
        day_filter = " AND substr({col}, 1, 10) = ?" if day is not None else ""
        args = (queue_id, day.isoformat()) if day is not None else (queue_id,)
        with closing(self._connect()) as conn:
            runs = conn.execute(
                "SELECT SUM(input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens) "
                "FROM prompt_runs WHERE queue_id = ?" + day_filter.format(col="started_at"), args
            ).fetchone()[0]
            replies = conn.execute(
                "SELECT SUM(input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens) "
                "FROM session_usage WHERE queue_id = ?" + day_filter.format(col="at"), args
            ).fetchone()[0]
            return (runs or 0) + (replies or 0)

    # --- token history (see token_stats) -------------------------------------

    def token_entries(self, start: str | None = None, end: str | None = None) -> list[tuple[str, int, str, int]]:
        """(day "YYYY-MM-DD", queue_id, model, tokens) per day, conversation
        and model, from headless runs (per model; runs without a per-model
        breakdown as "unknown") and remote-control replies. start/end are
        "YYYY-MM-DD", end exclusive."""
        def total(alias: str = "") -> str:
            return " + ".join(f"{alias}{c}" for c in ("input_tokens", "output_tokens", "cache_read_tokens",
                                                       "cache_creation_tokens"))

        t, rt, mt = total(), total("r."), total("m.")
        sql = (
            "SELECT day, queue_id, model, SUM(t) FROM ("
            f" SELECT substr(r.started_at, 1, 10) AS day, r.queue_id AS queue_id, m.model AS model, {mt} AS t"
            " FROM run_model_usage m JOIN prompt_runs r ON r.id = m.prompt_run_id"
            " UNION ALL"
            f" SELECT substr(r.started_at, 1, 10), r.queue_id, 'unknown', {rt} FROM prompt_runs r"
            " WHERE NOT EXISTS (SELECT 1 FROM run_model_usage m WHERE m.prompt_run_id = r.id)"
            " UNION ALL"
            f" SELECT substr(at, 1, 10), queue_id, model, {t} FROM session_usage"
            ") WHERE t > 0 AND day >= ? AND day < ? GROUP BY day, queue_id, model ORDER BY day"
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, (start or "0000", end or "9999")).fetchall()
            return [(r[0], r[1], r[2], r[3]) for r in rows]

    def run_status_counts(self, start: str | None = None, end: str | None = None) -> dict[int, tuple[int, int, int]]:
        """{queue_id: (success, errors, rate_limited)} of runs started in [start, end)."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT queue_id, SUM(status = ?), SUM(status = ?), SUM(status = ?) FROM prompt_runs "
                "WHERE substr(started_at, 1, 10) >= ? AND substr(started_at, 1, 10) < ? GROUP BY queue_id",
                (RunStatus.SUCCESS.value, RunStatus.ERROR.value, RunStatus.RATE_LIMITED.value,
                 start or "0000", end or "9999"),
            ).fetchall()
            return {r[0]: (r[1] or 0, r[2] or 0, r[3] or 0) for r in rows}

    def conversation_names(self) -> dict[int, tuple[str, bool]]:
        """{queue_id: (name, deleted)} for current and deleted conversations."""
        with closing(self._connect()) as conn:
            names = {r[0]: (r[1], True) for r in conn.execute("SELECT queue_id, name FROM deleted_queues")}
            names.update({r[0]: (r[1], False) for r in conn.execute("SELECT id, name FROM queues")})
            return names

    def blocked_seconds_total(self) -> float:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT SUM((julianday(finished_at) - julianday(started_at)) * 86400) "
                "FROM prompt_runs WHERE status = ? AND finished_at IS NOT NULL",
                (RunStatus.RATE_LIMITED.value,),
            ).fetchone()
            return row[0] or 0.0

    @staticmethod
    def _row_to_queue(row: sqlite3.Row) -> Queue:
        return Queue(
            id=row["id"],
            name=row["name"],
            project_path=row["project_path"],
            session_id=row["session_id"],
            status=QueueStatus(row["status"]),
            next_retry_at=datetime.fromisoformat(row["next_retry_at"]) if row["next_retry_at"] else None,
            last_error=row["last_error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            always_resume=bool(row["always_resume"]),
            delivery_mode=DeliveryMode(row["delivery_mode"]),
            launch_flags=json.loads(row["launch_flags"]) if row["launch_flags"] else None,
            first_run_flags=json.loads(row["first_run_flags"]) if row["first_run_flags"] else None,
            auto_continue=bool(row["auto_continue"]),
            paused=bool(row["paused"]),
            archived=bool(row["archived"]),
            claude_stopped=bool(row["claude_stopped"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]) if row["last_seen_at"] else None,
            remote_control_url=row["remote_control_url"],
            routine_id=row["routine_id"],
            live_busy=bool(row["live_busy"]),
            context_percent=row["context_percent"],
            context_tokens=row["context_tokens"],
            context_window=row["context_window"],
            context_model=row["context_model"],
            profile=row["profile"] or "",
        )

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> QueueItem:
        return QueueItem(
            id=row["id"],
            queue_id=row["queue_id"],
            prompt_text=row["prompt_text"],
            position=row["position"],
            status=ItemStatus(row["status"]),
            added_at=datetime.fromisoformat(row["added_at"]),
            continue_on_retry=bool(row["continue_on_retry"]),
            not_before=datetime.fromisoformat(row["not_before"]) if row["not_before"] else None,
            routine_id=row["routine_id"],
            model=row["model"],
        )

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> PromptRun:
        return PromptRun(
            id=row["id"],
            queue_id=row["queue_id"],
            queue_item_id=row["queue_item_id"],
            prompt_text=row["prompt_text"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            status=RunStatus(row["status"]),
            cost_usd=row["cost_usd"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cache_read_tokens=row["cache_read_tokens"],
            cache_creation_tokens=row["cache_creation_tokens"],
            duration_ms=row["duration_ms"],
            num_turns=row["num_turns"],
            result_text=row["result_text"],
            error_text=row["error_text"],
            git_commit=row["git_commit"],
            git_note=row["git_note"],
            review_id=row["review_id"],
            workflow_run_id=row["workflow_run_id"],
        )
