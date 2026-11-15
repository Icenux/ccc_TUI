import sqlite3

from ccc.storage import Storage


def test_database_uses_wal(tmp_path):
    storage = Storage(tmp_path / "history.db")
    mode = sqlite3.connect(storage.db_path).execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_reader_is_not_blocked_by_open_writer(tmp_path):
    storage = Storage(tmp_path / "history.db")
    queue = storage.create_queue("a", "/tmp/a", "s1")
    writer = sqlite3.connect(storage.db_path)
    writer.execute("BEGIN IMMEDIATE")
    try:
        assert storage.find_queue(queue.id) is not None
    finally:
        writer.rollback()
        writer.close()


def test_mark_seen_survives_a_locked_database(tmp_path):
    storage = Storage(tmp_path / "history.db")
    queue = storage.create_queue("a", "/tmp/a", "s1")
    storage._connect = lambda: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked"))
    storage.mark_seen(queue.id)  # must not raise


def test_connection_is_wal_normal(tmp_path):
    storage = Storage(tmp_path / "history.db")
    conn = storage._connect()
    try:
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    finally:
        conn.close()
