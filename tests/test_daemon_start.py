from datetime import datetime

from ccc import daemon_start
from ccc.heartbeat import beat
from ccc.storage import Storage


def _record(monkeypatch, ok=True):
    calls = []
    monkeypatch.setattr(daemon_start, "_run", lambda *c: calls.append(c) or ok)
    return calls


def test_nothing_to_do_when_daemon_is_alive(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "h.db")
    beat(storage, datetime.now())
    calls = _record(monkeypatch)
    assert daemon_start.ensure_daemon_running(storage) is None
    assert calls == []


def test_starts_and_enables_unit_and_linger_when_dead(tmp_path, monkeypatch):
    calls = _record(monkeypatch)
    assert daemon_start.ensure_daemon_running(Storage(tmp_path / "h.db")) is True
    assert calls[0] == ("systemctl", "--user", "enable", "--now", "ccc.service")
    assert calls[1][:2] == ("loginctl", "enable-linger")


def test_failure_is_silent_and_skips_linger(tmp_path, monkeypatch):
    calls = _record(monkeypatch, ok=False)
    assert daemon_start.ensure_daemon_running(Storage(tmp_path / "h.db")) is False
    assert len(calls) == 1
