import sqlite3
from datetime import datetime

import pytest

from ccc.models import ItemStatus, PromptRun, QueueStatus, RunStatus
from ccc.storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "test.db")


def test_create_and_fetch_queue(storage):
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    assert q.id is not None
    assert storage.get_queue_by_path("/tmp/demo").id == q.id
    assert storage.get_queue_by_path("/nope") is None


def test_multiple_queues_can_share_a_project_path(storage):
    q1 = storage.create_queue("session-one", "/tmp/shared", "session-aaa")
    q2 = storage.create_queue("session-two", "/tmp/shared", "session-bbb")
    assert q1.id != q2.id
    assert storage.get_queue_by_session_id("session-aaa").id == q1.id
    assert storage.get_queue_by_session_id("session-bbb").id == q2.id
    # get_queue_by_path is only meaningful for the plain single-queue-per-path
    # flow; with two sharing a path it deterministically returns the oldest.
    assert storage.get_queue_by_path("/tmp/shared").id == q1.id


def test_migrates_old_schema_with_unique_project_path(tmp_path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE queues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            project_path TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'idle',
            next_retry_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE queue_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_id INTEGER NOT NULL REFERENCES queues(id),
            prompt_text TEXT NOT NULL,
            position INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            added_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO queues (id, name, project_path, session_id, status, created_at, updated_at) "
        "VALUES (1, 'old-queue', '/tmp/old-path', 'old-session', 'idle', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO queue_items (id, queue_id, prompt_text, position, status, added_at) "
        "VALUES (1, 1, 'still here', 0, 'pending', '2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    storage = Storage(db_path)  # triggers the migration on open

    queue = storage.get_queue_by_session_id("old-session")
    assert queue is not None
    assert queue.id == 1  # id preserved, so queue_items FK still resolves
    assert queue.project_path == "/tmp/old-path"
    item = storage.next_pending_item(queue.id)
    assert item.prompt_text == "still here"

    # a second queue can now share that same old path (the whole point)
    storage.create_queue("new-queue", "/tmp/old-path", "new-session")
    assert storage.get_queue_by_session_id("new-session") is not None


def test_add_item_and_pop_next_pending(storage):
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    storage.add_queue_item(q.id, "first")
    storage.add_queue_item(q.id, "second")
    first = storage.next_pending_item(q.id)
    assert first.prompt_text == "first"
    storage.set_item_status(first.id, ItemStatus.DONE)
    second = storage.next_pending_item(q.id)
    assert second.prompt_text == "second"


def test_update_queue_status_persists(storage):
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    retry_at = datetime(2026, 9, 3, 15, 30)
    storage.update_queue_status(q.id, QueueStatus.WAITING_RETRY, next_retry_at=retry_at, last_error="boom")
    reloaded = storage.get_queue(q.id)
    assert reloaded.status == QueueStatus.WAITING_RETRY
    assert reloaded.next_retry_at == retry_at
    assert reloaded.last_error == "boom"


def test_reset_running_items_to_pending(storage):
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    item = storage.add_queue_item(q.id, "first")
    storage.set_item_status(item.id, ItemStatus.RUNNING)
    storage.update_queue_status(q.id, QueueStatus.RUNNING)
    storage.reset_running_items_to_pending()
    assert storage.next_pending_item(q.id).id == item.id
    assert storage.get_queue(q.id).status == QueueStatus.IDLE


def test_insert_and_list_runs(storage):
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    run_id = storage.insert_prompt_run(
        PromptRun(
            id=0,
            queue_id=q.id,
            queue_item_id=None,
            prompt_text="hi",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            status=RunStatus.SUCCESS,
            cost_usd=0.01,
            input_tokens=5,
            output_tokens=5,
        )
    )
    assert run_id > 0
    runs = storage.runs_for_queue(q.id)
    assert len(runs) == 1
    assert runs[0].cost_usd == 0.01


def test_total_tokens_by_model(storage):
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    run_id = storage.insert_prompt_run(
        PromptRun(
            id=0,
            queue_id=q.id,
            queue_item_id=None,
            prompt_text="hi",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            status=RunStatus.SUCCESS,
        )
    )
    storage.insert_run_model_usage(run_id, "claude-sonnet-5", input_tokens=10, output_tokens=5, cache_read_tokens=100)
    storage.insert_run_model_usage(run_id, "claude-opus-5", input_tokens=2, output_tokens=1)

    run_id2 = storage.insert_prompt_run(
        PromptRun(
            id=0,
            queue_id=q.id,
            queue_item_id=None,
            prompt_text="hi again",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            status=RunStatus.SUCCESS,
        )
    )
    storage.insert_run_model_usage(run_id2, "claude-sonnet-5", input_tokens=20, output_tokens=10)

    totals = storage.total_tokens_by_model()
    assert totals[0] == ("claude-sonnet-5", 10 + 5 + 100 + 20 + 10)
    assert totals[1] == ("claude-opus-5", 3)


def test_daily_token_totals_sums_all_token_fields(storage):
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    storage.insert_prompt_run(
        PromptRun(
            id=0,
            queue_id=q.id,
            queue_item_id=None,
            prompt_text="hi",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            status=RunStatus.SUCCESS,
            input_tokens=2,
            output_tokens=4,
            cache_read_tokens=100,
            cache_creation_tokens=50,
        )
    )
    totals = storage.daily_token_totals()
    assert len(totals) == 1
    day, total = totals[0]
    assert total == 156


def test_claim_next_pending_item_marks_running_and_returns_it(storage):
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    storage.add_queue_item(q.id, "first")
    storage.add_queue_item(q.id, "second")

    claimed = storage.claim_next_pending_item(q.id)
    assert claimed.prompt_text == "first"
    assert claimed.status == ItemStatus.RUNNING

    # peeking with next_pending_item now sees "second", "first" is gone from pending
    assert storage.next_pending_item(q.id).prompt_text == "second"


def test_claim_next_pending_item_is_exclusive_across_storage_instances(storage, tmp_path):
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    storage.add_queue_item(q.id, "only item")

    other = Storage(tmp_path / "test.db")  # separate connection, same file

    first_claim = storage.claim_next_pending_item(q.id)
    second_claim = other.claim_next_pending_item(q.id)

    assert first_claim is not None
    assert second_claim is None  # already claimed by `storage`, nothing left to take


def test_claim_next_pending_item_empty_queue_returns_none(storage):
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    assert storage.claim_next_pending_item(q.id) is None


def test_count_pending_items(storage):
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    assert storage.count_pending_items(q.id) == 0
    storage.add_queue_item(q.id, "a")
    storage.add_queue_item(q.id, "b")
    storage.add_queue_item(q.id, "c")
    assert storage.count_pending_items(q.id) == 3
    claimed = storage.claim_next_pending_item(q.id)
    assert storage.count_pending_items(q.id) == 2
    storage.set_item_status(claimed.id, ItemStatus.DONE)
    assert storage.count_pending_items(q.id) == 2


def test_blocked_seconds_total(storage):
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    started = datetime(2026, 9, 3, 10, 0, 0)
    finished = datetime(2026, 9, 3, 10, 5, 0)
    storage.insert_prompt_run(
        PromptRun(
            id=0,
            queue_id=q.id,
            queue_item_id=None,
            prompt_text="hi",
            started_at=started,
            finished_at=finished,
            status=RunStatus.RATE_LIMITED,
        )
    )
    assert storage.blocked_seconds_total() == pytest.approx(300, abs=1)


import sqlite3 as _sqlite3


def test_launch_flags_round_trip(tmp_path):
    storage = Storage(tmp_path / "h.db")
    q = storage.create_queue("a", "/tmp/a", "s1", launch_flags=["--model", "opus"], first_run_flags=["--fork-session"])
    q = storage.get_queue(q.id)
    assert q.launch_flags == ["--model", "opus"]
    assert q.first_run_flags == ["--fork-session"]
    assert storage.create_queue("b", "/tmp/b", "s2").launch_flags is None


def test_migration_adds_launch_columns_to_old_db(tmp_path):
    db = tmp_path / "old.db"
    conn = _sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE queues (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, project_path TEXT NOT NULL, "
        "session_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'idle', next_retry_at TEXT, last_error TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, always_resume INTEGER NOT NULL DEFAULT 0, "
        "delivery_mode TEXT NOT NULL DEFAULT 'headless')"
    )
    conn.execute("INSERT INTO queues (name, project_path, session_id, created_at, updated_at) VALUES ('x','/x','sx','2026-01-01T00:00:00','2026-01-01T00:00:00')")
    conn.commit(); conn.close()
    q = Storage(db).list_queues()[0]
    assert q.launch_flags is None and q.first_run_flags is None


def test_set_queue_session_adopts_id_and_clears_first_run(tmp_path):
    storage = Storage(tmp_path / "h.db")
    q = storage.create_queue("a", "/tmp/a", "placeholder", first_run_flags=["--fork-session"])
    storage.set_queue_session(q.id, "real-id")
    q = storage.get_queue(q.id)
    assert (q.session_id, q.first_run_flags, q.always_resume) == ("real-id", None, True)


def test_set_queue_session_collision_raises(tmp_path):
    storage = Storage(tmp_path / "h.db")
    storage.create_queue("a", "/tmp/a", "taken")
    q = storage.create_queue("b", "/tmp/b", "placeholder")
    with pytest.raises(_sqlite3.IntegrityError):
        storage.set_queue_session(q.id, "taken")


def test_recent_project_paths_and_pending_items(tmp_path):
    storage = Storage(tmp_path / "h.db")
    a = storage.create_queue("a", "/tmp/a", "s1")
    storage.create_queue("b", "/tmp/b", "s2")
    storage.create_queue("a2", "/tmp/a", "s3")
    storage.update_queue_status(a.id, QueueStatus.IDLE)  # bumps updated_at
    assert storage.recent_project_paths()[0] == "/tmp/a"
    assert set(storage.recent_project_paths()) == {"/tmp/a", "/tmp/b"}
    storage.add_queue_item(a.id, "one"); storage.add_queue_item(a.id, "two")
    assert [i.prompt_text for i in storage.list_pending_items(a.id)] == ["one", "two"]


def test_auto_continue_and_paused_defaults_and_setters(tmp_path):
    storage = Storage(tmp_path / "h.db")
    q = storage.create_queue("a", "/tmp/a", "s1")
    assert q.auto_continue is True and q.paused is False
    assert storage.create_queue("b", "/tmp/b", "s2", auto_continue=False).auto_continue is False
    storage.set_queue_auto_continue(q.id, False)
    storage.set_queue_paused(q.id, True)
    q = storage.get_queue(q.id)
    assert q.auto_continue is False and q.paused is True


def test_migration_adds_auto_continue_paused_and_continue_flag(tmp_path):
    db = tmp_path / "old.db"
    conn = _sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE queues (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, project_path TEXT NOT NULL, "
        "session_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'idle', next_retry_at TEXT, last_error TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE queue_items (id INTEGER PRIMARY KEY AUTOINCREMENT, queue_id INTEGER NOT NULL, "
        "prompt_text TEXT NOT NULL, position INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', added_at TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO queues (name, project_path, session_id, created_at, updated_at) VALUES ('x','/x','sx','2026-01-01T00:00:00','2026-01-01T00:00:00')")
    conn.execute("INSERT INTO queue_items (queue_id, prompt_text, position, added_at) VALUES (1, 'p', 0, '2026-01-01T00:00:00')")
    conn.commit(); conn.close()
    storage = Storage(db)
    q = storage.list_queues()[0]
    assert q.auto_continue is True and q.paused is False
    assert storage.list_pending_items(q.id)[0].continue_on_retry is False


def test_item_editing_moving_and_skipping(tmp_path):
    storage = Storage(tmp_path / "h.db")
    q = storage.create_queue("a", "/tmp/a", "s1")
    one = storage.add_queue_item(q.id, "one")
    two = storage.add_queue_item(q.id, "two")
    done = storage.add_queue_item(q.id, "done")
    three = storage.add_queue_item(q.id, "three")
    storage.set_item_status(done.id, ItemStatus.DONE)

    def order():
        return [i.prompt_text for i in storage.list_pending_items(q.id)]

    assert storage.move_item(three.id, -1) is True  # skips over the non-pending one
    assert order() == ["one", "three", "two"]
    assert storage.move_item(one.id, -1) is False  # already first
    assert storage.move_item(one.id, +1) is True
    assert order() == ["three", "one", "two"]
    storage.update_item_text(two.id, "two edited")
    storage.skip_item(one.id)
    assert order() == ["three", "two edited"]
    storage.set_item_continue_on_retry(two.id, True)
    assert storage.list_pending_items(q.id)[1].continue_on_retry is True


def test_settings(tmp_path):
    storage = Storage(tmp_path / "h.db")
    assert storage.get_setting("continue_text") is None
    assert storage.get_setting("continue_text", "dflt") == "dflt"
    storage.set_setting("continue_text", "go on")
    storage.set_setting("continue_text", "keep going")
    assert storage.get_setting("continue_text") == "keep going"


def test_archive_and_find_queue(tmp_path):
    storage = Storage(tmp_path / "h.db")
    q = storage.create_queue("a", "/tmp/a", "s1")
    assert q.archived is False
    storage.set_queue_archived(q.id, True)
    assert storage.get_queue(q.id).archived is True
    assert storage.find_queue(q.id).id == q.id
    assert storage.find_queue(9999) is None


def test_delete_queue_removes_conversation_but_keeps_token_history(tmp_path):
    storage = Storage(tmp_path / "h.db")
    keep = storage.create_queue("keep", "/tmp/k", "sk")
    q = storage.create_queue("a", "/tmp/a", "s1")
    item = storage.add_queue_item(q.id, "p")
    run_id = storage.insert_prompt_run(
        PromptRun(id=0, queue_id=q.id, queue_item_id=item.id, prompt_text="p",
                  started_at=datetime.now(), finished_at=datetime.now(), status=RunStatus.SUCCESS)
    )
    storage.insert_run_model_usage(run_id, "m", input_tokens=1, output_tokens=1, cache_read_tokens=0, cache_creation_tokens=0)
    storage.add_queue_item(keep.id, "stays")
    storage.delete_queue(q.id)
    assert storage.find_queue(q.id) is None
    assert storage.list_pending_items(q.id) == []
    assert [i.prompt_text for i in storage.list_pending_items(keep.id)] == ["stays"]
    # texts are gone, the numbers stay under the conversation's old name
    assert [r.prompt_text for r in storage.runs_for_queue(q.id)] == [""]
    assert storage.total_tokens_by_model() == [("m", 2)]
    assert storage.conversation_names()[q.id] == ("a", True)
    assert storage.conversation_names()[keep.id] == ("keep", False)


def test_unseen_queue_ids(tmp_path):
    from datetime import timedelta

    storage = Storage(tmp_path / "h.db")
    q = storage.create_queue("a", "/tmp/a", "s1")
    other = storage.create_queue("b", "/tmp/b", "s2")
    assert storage.unseen_queue_ids() == set()
    finished = datetime.now()
    storage.insert_prompt_run(PromptRun(id=0, queue_id=q.id, queue_item_id=None, prompt_text="p",
                                        started_at=finished, finished_at=finished, status=RunStatus.SUCCESS))
    assert storage.unseen_queue_ids() == {q.id}
    storage.mark_seen(q.id)
    assert storage.unseen_queue_ids() == set()
    later = datetime.now() + timedelta(seconds=5)
    storage.insert_prompt_run(PromptRun(id=0, queue_id=q.id, queue_item_id=None, prompt_text="p2",
                                        started_at=later, finished_at=later, status=RunStatus.SUCCESS))
    assert storage.unseen_queue_ids() == {q.id}
    assert other.id not in storage.unseen_queue_ids()


def test_migration_marks_existing_conversations_seen(tmp_path):
    db = tmp_path / "old.db"
    conn = _sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE queues (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, project_path TEXT NOT NULL, "
        "session_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'idle', next_retry_at TEXT, last_error TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE prompt_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, queue_id INTEGER NOT NULL, queue_item_id INTEGER, "
        "prompt_text TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, cost_usd REAL NOT NULL DEFAULT 0, "
        "input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0, "
        "cache_creation_tokens INTEGER NOT NULL DEFAULT 0, duration_ms INTEGER NOT NULL DEFAULT 0, num_turns INTEGER NOT NULL DEFAULT 0, "
        "result_text TEXT, error_text TEXT)"
    )
    conn.execute("INSERT INTO queues (name, project_path, session_id, created_at, updated_at) VALUES ('x','/x','sx','2026-01-01T00:00:00','2026-01-01T00:00:00')")
    conn.execute("INSERT INTO prompt_runs (queue_id, prompt_text, started_at, finished_at, status) VALUES (1,'p','2026-01-01T00:00:00','2026-01-01T00:01:00','success')")
    conn.commit(); conn.close()
    assert Storage(db).unseen_queue_ids() == set()


def test_remote_control_url_and_front_insert(tmp_path):
    storage = Storage(tmp_path / "h.db")
    q = storage.create_queue("a", "/tmp/a", "s1")
    assert q.remote_control_url is None
    storage.set_remote_control_url(q.id, "https://claude.ai/code/session_x")
    assert storage.get_queue(q.id).remote_control_url == "https://claude.ai/code/session_x"
    storage.add_queue_item(q.id, "one")
    storage.add_queue_item(q.id, "two")
    storage.add_queue_item_front(q.id, "continue")
    assert [i.prompt_text for i in storage.list_pending_items(q.id)] == ["continue", "one", "two"]


def test_item_schedule(tmp_path):
    storage = Storage(tmp_path / "h.db")
    q = storage.create_queue("a", "/tmp/a", "s1")
    at = datetime(2026, 10, 13, 22, 0)
    item = storage.add_queue_item(q.id, "later", not_before=at)
    assert item.not_before == at
    storage.update_item_schedule(item.id, None)
    assert storage.list_pending_items(q.id)[0].not_before is None


def _blocked_by(storage, queue, item):
    storage.set_item_status(item.id, ItemStatus.FAILED)
    storage.insert_prompt_run(PromptRun(
        id=0, queue_id=queue.id, queue_item_id=item.id, prompt_text=item.prompt_text,
        started_at=datetime.now(), finished_at=datetime.now(), status=RunStatus.ERROR, error_text="boom"))
    storage.update_queue_status(queue.id, QueueStatus.BLOCKED_ERROR, last_error="boom")


def test_retry_puts_the_failed_task_back_first(storage):
    queue = storage.create_queue("p", "/tmp/p", "s")
    failed = storage.add_queue_item(queue.id, "flaky")
    storage.add_queue_item(queue.id, "next")
    _blocked_by(storage, queue, failed)

    assert storage.retry_queue(queue.id)
    queue = storage.get_queue(queue.id)
    assert queue.status == QueueStatus.IDLE and queue.last_error is None
    assert [i.prompt_text for i in storage.list_pending_items(queue.id)] == ["flaky", "next"]


def test_retry_leaves_older_failures_alone(storage):
    # Only the run that blocked the queue (its latest) is tried again.
    queue = storage.create_queue("p", "/tmp/p", "s")
    old = storage.add_queue_item(queue.id, "old")
    _blocked_by(storage, queue, old)
    storage.update_queue_status(queue.id, QueueStatus.IDLE)
    ok = storage.add_queue_item(queue.id, "fine")
    storage.set_item_status(ok.id, ItemStatus.DONE)
    storage.insert_prompt_run(PromptRun(
        id=0, queue_id=queue.id, queue_item_id=ok.id, prompt_text="fine",
        started_at=datetime.now(), finished_at=datetime.now(), status=RunStatus.SUCCESS))
    storage.update_queue_status(queue.id, QueueStatus.BLOCKED_ERROR, last_error="session conflict")

    assert storage.retry_queue(queue.id)
    assert storage.list_pending_items(queue.id) == []


def test_retry_resumes_a_queue_paused_at_a_limit(storage):
    queue = storage.create_queue("p", "/tmp/p", "s")
    storage.update_queue_status(queue.id, QueueStatus.PAUSED_LIMIT)
    assert storage.retry_queue(queue.id)
    assert storage.get_queue(queue.id).status == QueueStatus.IDLE


def test_retry_does_nothing_to_a_working_queue(storage):
    queue = storage.create_queue("p", "/tmp/p", "s")
    storage.update_queue_status(queue.id, QueueStatus.RUNNING)
    assert not storage.retry_queue(queue.id)
    assert storage.get_queue(queue.id).status == QueueStatus.RUNNING


def test_live_work_in_claude_code_shows_busy_then_unseen(tmp_path):
    from datetime import timedelta

    storage = Storage(tmp_path / "h.db")
    q = storage.create_queue("a", "/tmp/a", "s1")
    storage.mark_seen(q.id)
    storage.set_live_busy(q.id, True)
    assert storage.get_queue(q.id).live_busy
    assert storage.unseen_queue_ids() == set()  # still working: nothing finished yet
    storage.set_live_busy(q.id, False)
    assert not storage.get_queue(q.id).live_busy
    assert storage.unseen_queue_ids() == {q.id}  # it finished while you weren't looking
    storage.set_live_busy(q.id, False)  # idle again and again: no new finish
    storage.mark_seen(q.id)
    assert storage.unseen_queue_ids() == set()
    assert datetime.now() + timedelta(seconds=1) > storage.get_queue(q.id).last_seen_at


def test_turning_auto_continue_on_wakes_a_queue_stopped_at_the_limit(storage):
    """#243: t → o on a conversation already paused at the limit must make it carry on."""
    from datetime import datetime, timedelta

    from ccc.models import QueueStatus

    q = storage.create_queue("demo", "/tmp/demo", "session-abc", auto_continue=False)
    until = datetime.now() + timedelta(hours=1)
    storage.update_queue_status(q.id, QueueStatus.PAUSED_LIMIT, next_retry_at=until)

    storage.set_queue_auto_continue(q.id, True)
    after = storage.get_queue(q.id)
    assert after.auto_continue is True
    assert after.status == QueueStatus.WAITING_RETRY
    assert after.next_retry_at == until

    storage.set_queue_auto_continue(q.id, False)
    assert storage.get_queue(q.id).status == QueueStatus.PAUSED_LIMIT


def test_migration_drops_the_reverted_by_column_and_keeps_the_runs(tmp_path):
    db = tmp_path / "old.db"
    first = Storage(db)
    queue = first.create_queue("a", "/tmp/a", "s1")
    now = datetime.now()
    first.insert_prompt_run(PromptRun(id=0, queue_id=queue.id, queue_item_id=None, prompt_text="p", started_at=now,
                                      finished_at=now, status=RunStatus.SUCCESS, input_tokens=5))
    conn = sqlite3.connect(db)  # the schema of a ccc from before the revert feature was removed
    conn.execute("ALTER TABLE prompt_runs ADD COLUMN reverted_by TEXT")
    conn.execute("UPDATE prompt_runs SET reverted_by = 'abc1234'")
    conn.commit()
    conn.close()

    storage = Storage(db)

    columns = {row[1] for row in sqlite3.connect(db).execute("PRAGMA table_info(prompt_runs)")}
    assert "reverted_by" not in columns
    assert [(r.prompt_text, r.input_tokens) for r in storage.runs_for_queue(queue.id)] == [("p", 5)]


def test_claude_stopped_is_cleared_by_a_new_message_or_task(storage):
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    assert not storage.get_queue(q.id).claude_stopped
    storage.set_claude_stopped(q.id, True)
    assert storage.get_queue(q.id).claude_stopped
    storage.add_queue_item(q.id, "go on")
    assert not storage.get_queue(q.id).claude_stopped
    storage.set_claude_stopped(q.id, True)
    storage.add_queue_item_front(q.id, "continue")
    assert not storage.get_queue(q.id).claude_stopped


def test_claude_stopped_column_is_added_to_an_old_database(tmp_path):
    path = tmp_path / "old.db"
    Storage(path)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE queues DROP COLUMN claude_stopped")
    storage = Storage(path)
    q = storage.create_queue("demo", "/tmp/demo", "session-abc")
    assert not storage.get_queue(q.id).claude_stopped
