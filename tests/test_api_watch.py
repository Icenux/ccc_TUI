import asyncio
import logging
import subprocess

import pytest

from ccc import daemon, pairing


def fake_systemctl(monkeypatch, states: dict[str, str], start_ok: bool = True):
    """Answers `systemctl --user <verb> ccc-api.service` from `states`; records the verbs."""
    calls: list[str] = []

    def run(cmd, **kwargs):
        verb = cmd[2]
        calls.append(verb)
        if verb == "start":
            return subprocess.CompletedProcess(cmd, 0 if start_ok else 1, "", "")
        return subprocess.CompletedProcess(cmd, 0, states.get(verb, "") + "\n", "")

    monkeypatch.setattr(pairing.subprocess, "run", run)
    return calls


def test_enabled_but_stopped_api_is_started(monkeypatch):
    calls = fake_systemctl(monkeypatch, {"is-enabled": "enabled", "is-active": "inactive"})
    assert pairing.ensure_api_running() is True
    assert calls == ["is-enabled", "is-active", "start"]


def test_failed_api_is_started(monkeypatch):
    fake_systemctl(monkeypatch, {"is-enabled": "enabled", "is-active": "failed"})
    assert pairing.ensure_api_running() is True


def test_start_that_does_not_go_through_is_reported(monkeypatch):
    fake_systemctl(monkeypatch, {"is-enabled": "enabled", "is-active": "inactive"}, start_ok=False)
    assert pairing.ensure_api_running() is False


@pytest.mark.parametrize("state", ["active", "activating", "reloading"])
def test_running_or_restarting_api_is_left_alone(monkeypatch, state):
    calls = fake_systemctl(monkeypatch, {"is-enabled": "enabled", "is-active": state})
    assert pairing.ensure_api_running() is None
    assert "start" not in calls


@pytest.mark.parametrize("enabled", ["disabled", "masked", "static", "not-found", ""])
def test_api_the_user_did_not_turn_on_is_never_started(monkeypatch, enabled):
    calls = fake_systemctl(monkeypatch, {"is-enabled": enabled, "is-active": "inactive"})
    assert pairing.ensure_api_running() is None
    assert calls == ["is-enabled"]


def test_without_systemctl_nothing_happens(monkeypatch):
    def run(cmd, **kwargs):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(pairing.subprocess, "run", run)
    assert pairing.ensure_api_running() is None


async def run_loop(monkeypatch, answers: list):
    """Runs api_loop through the given answers of ensure_api_running, then cancels it."""
    queue = list(answers)
    done = asyncio.Event()

    def ensure():
        result = queue.pop(0)
        if not queue:
            done.set()
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(pairing, "ensure_api_running", ensure)
    task = asyncio.create_task(daemon.api_loop(interval=0.01))
    await asyncio.wait_for(done.wait(), 5)
    await asyncio.sleep(0.05)
    task.cancel()
    return task


async def test_loop_starts_a_down_api_and_logs_it(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="ccc.daemon"):
        await run_loop(monkeypatch, [True, None])
    assert "started it" in caplog.text


async def test_loop_reports_an_api_that_cannot_start_once_per_outage(monkeypatch, caplog):
    with caplog.at_level(logging.ERROR, logger="ccc.daemon"):
        await run_loop(monkeypatch, [False, False, False, None, False])
    assert caplog.text.count("could not be started") == 2  # the second outage is a new report


async def test_loop_survives_a_failing_check(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="ccc.daemon"):
        task = await run_loop(monkeypatch, [RuntimeError("boom"), True])
    assert "checking ccc-api.service failed" in caplog.text
    assert "started it" in caplog.text  # the loop went on to the next answer
    with pytest.raises(asyncio.CancelledError):
        await task
