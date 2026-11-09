import sqlite3

from ccc import api, pairing
from ccc.storage import Storage

from test_api import _call, server, storage  # noqa: F401  (fixtures)


def test_a_database_from_the_time_of_roles_still_works(tmp_path):
    path = tmp_path / "ccc.db"
    Storage(str(path))
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE devices ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
        conn.execute("INSERT INTO devices (name, token_sha256, created_at, role) VALUES ('Old', 'x', '2026-10-01T10:00:00', 'read')")
    reopened = Storage(str(path))
    assert [d.name for d in reopened.list_devices()] == ["Old"]
    assert api.add_device(reopened, "New")[0].name == "New"


def test_a_pairing_code_made_while_roles_existed_still_redeems(storage):  # noqa: F811
    code, _expires = pairing.new_code(storage)
    stored = storage.get_setting(pairing.CODE_KEY)
    assert stored.count("|") == 1
    storage.set_setting(pairing.CODE_KEY, stored + "|operate")
    assert pairing.redeem(storage, code, "Phone")[0].name == "Phone"


def test_every_device_reaches_settings_routines_and_the_terminal(server, storage):  # noqa: F811
    base, _token = server
    token = api.add_device(storage, "Phone")[1]
    for path in ("settings", "routines", "audit"):
        assert _call(f"{base}/api/v1/{path}", token)[0] == 200
