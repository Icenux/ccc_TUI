"""The agents and views parts of the HTTP API (ccc.api_agents, ccc.api_views)."""
import pytest

from ccc import api, api_scope
from ccc.models import ReviewStatus, Verdict
from ccc.session_discovery import DiscoveredSession
from ccc.storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(str(tmp_path / "ccc.db"))


def _queue(storage, tmp_path, name="shop", session="s1"):
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    return storage.create_queue(name, str(folder), session)


def _error(call) -> api.ApiError:
    with pytest.raises(api.ApiError) as error:
        call()
    return error.value


# --- reviews, goals, workflows, questions -------------------------------------------


def test_review_request_stop_and_send_findings(storage, tmp_path):
    q = _queue(storage, tmp_path)
    endpoint = api.Api(storage)

    status, listed = endpoint.route("POST", f"/api/v1/conversations/{q.id}/reviews", {},
                                    {"instructions": "check the API", "until_pass": True, "rounds": 3})
    assert status == 201 and listed["active"]
    review = listed["reviews"][0]
    assert (review["instructions"], review["until_pass"], review["max_rounds"], review["status_label"]) == \
        ("check the API", True, 3, "waiting")
    assert _error(lambda: endpoint.request_review(q.id, {})).status == 400  # one already waits

    endpoint.route("POST", f"/api/v1/conversations/{q.id}/reviews/stop", {}, {})
    assert not endpoint.reviews(q.id)["active"]

    storage.update_review(review["id"], status=ReviewStatus.DONE, verdict=Verdict.FAIL, report="1. a bug")
    assert endpoint.reviews(q.id)["reviews"][0]["can_apply"]
    endpoint.route("POST", f"/api/v1/reviews/{review['id']}/apply", {}, {})
    assert endpoint.reviews(q.id)["reviews"][0]["fix_sent"]
    assert storage.list_pending_items(q.id)
    assert _error(lambda: endpoint.apply_review(review["id"])).status == 409


def test_goal_start_and_stop(storage, tmp_path):
    q = _queue(storage, tmp_path)
    endpoint = api.Api(storage)

    goals = endpoint.start_goal(q.id, {"goal": "expired tokens are rejected", "check": "pytest -q", "rounds": 4})
    goal = goals["goals"][0]
    assert goals["active"] and (goal["check_command"], goal["max_rounds"]) == ("pytest -q", 4)
    assert goal["phase_label"] == "Claude works on round 1"
    assert len(storage.list_pending_items(q.id)) == 1
    assert _error(lambda: endpoint.start_goal(q.id, {"goal": "  "})).status == 400

    assert not endpoint.route("POST", f"/api/v1/conversations/{q.id}/goals/stop", {}, {})[1]["active"]


def test_workflow_templates_start_answer_stop(storage, tmp_path):
    q = _queue(storage, tmp_path)
    endpoint = api.Api(storage)

    templates = endpoint.workflow_templates()
    assert {t["name"] for t in templates} >= {"plan-build-review", "full"}
    assert all(t["steps"] for t in templates)

    flows = endpoint.start_workflow(q.id, {"template": "plan-build-review", "task": "add caching"})
    run = flows["runs"][0]
    assert flows["active"] and run["task"] == "add caching"
    assert [s["state"] for s in run["steps"]][0] == "now"
    assert _error(lambda: endpoint.start_workflow(q.id, {"template": "nope", "task": "x"})).status == 400
    assert _error(lambda: endpoint.answer_workflow(q.id, {"approve": True})).status == 409  # no gate waits

    assert not endpoint.stop_workflow(q.id)["active"]


# --- routines, live sessions, analytics, settings --------------------------------------


def test_routines_add_edit_toggle_run_delete(storage, tmp_path):
    q = _queue(storage, tmp_path)
    endpoint = api.Api(storage)

    listed = endpoint.route("POST", "/api/v1/routines", {}, {
        "conversation_id": q.id, "name": "Nightly", "prompt": "update deps", "kind": "daily", "value": "22:00"})[1]
    routine = listed["routines"][0]
    assert routine["schedule"] and routine["enabled"] and routine["next_run_at"]
    assert {k["kind"] for k in listed["kinds"]} >= {"daily", "cron"}

    endpoint.edit_routine(routine["id"], {"conversation_id": q.id, "name": "Nightly", "prompt": "update deps",
                                          "kind": "cron", "value": "0 3 * * *"})
    assert endpoint.routines()["routines"][0]["kind"] == "cron"
    assert endpoint.enable_routine(routine["id"], {"enabled": False})["routines"][0]["enabled"] is False
    ran = endpoint.run_routine(routine["id"])
    assert ran["status"] == "queued" and storage.list_pending_items(q.id)[0].prompt_text == "update deps"
    assert _error(lambda: endpoint.add_routine({"conversation_id": q.id, "prompt": "x", "kind": "daily",
                                                "value": "25:99"})).status == 400
    endpoint.route("DELETE", f"/api/v1/routines/{routine['id']}", {}, {})
    assert endpoint.routines()["routines"] == []


def test_live_sessions_and_a_prompt_for_one(storage, tmp_path, monkeypatch):
    session = DiscoveredSession("1b2c-3d", "refactor-auth", str(tmp_path), "interactive", "idle")
    monkeypatch.setattr("ccc.session_discovery.discover_sessions", lambda: [session])
    api_scope.add_root(storage, str(tmp_path))
    endpoint = api.Api(storage)

    assert endpoint.live_sessions()[0]["name"] == "refactor-auth"
    status, sent = endpoint.route("POST", "/api/v1/live-sessions/1b2c-3d/prompt", {}, {"prompt": "go on"})
    assert status == 201
    assert storage.list_pending_items(sent["conversation_id"])[0].prompt_text == "go on"
    assert _error(lambda: endpoint.prompt_live_session("gone", {"prompt": "x"})).status == 404


def test_analytics_of_a_month(storage):
    result = api.Api(storage).route("GET", "/api/v1/analytics", {"year": "2026", "month": "9"}, {})[1]
    assert (result["year"], result["month"], result["total"]) == (2026, 9, 0)
    assert result["title"] and result["by_day"] == []
    assert _error(lambda: api.Api(storage).analytics(2026, 13)).status == 400


def test_settings_read_change_and_refuse(storage):
    endpoint = api.Api(storage)
    before = endpoint.settings()
    assert before["telegram"] == {"token_set": False, "chat": "", "on": False}

    after = endpoint.route("POST", "/api/v1/settings", {}, {
        "usage_reserve": 80,
        "telegram_token": "123:abc", "telegram_chat": "42", "groups": "own", "continue_text": "go on"})[1]
    assert (after["usage_reserve"], after["continue_text"]) == (80, "go on")
    assert not any(key.startswith("context") for key in after)  # the context fill is gone
    assert after["telegram"] == {"token_set": True, "chat": "42", "on": True}
    assert "123:abc" not in str(after)  # the token never leaves ccc

    assert _error(lambda: endpoint.change_settings({"usage_reserve": 33})).status == 400
    assert _error(lambda: endpoint.change_settings({"discord_webhook": "https://example.com/x"})).status == 400
    assert endpoint.settings()["usage_reserve"] == 80
    assert endpoint.change_settings({"usage_reserve": None})["usage_reserve"] is None


def test_telegram_test_never_shows_the_token(storage, monkeypatch):
    def fail(token, chat, text):
        raise ConnectionError(f"https://api.telegram.org/bot{token}/sendMessage failed")

    monkeypatch.setattr("ccc.notify.send_telegram", fail)
    endpoint = api.Api(storage)
    assert _error(endpoint.test_telegram).status == 409  # not set up
    endpoint.change_settings({"telegram_token": "123:secret", "telegram_chat": "42"})
    error = _error(endpoint.test_telegram)
    assert error.status == 502 and "secret" not in error.message and "ConnectionError" in error.message
    assert endpoint.notifications_off("telegram")["telegram"]["on"] is False


def test_prompt_and_launch_presets_saved(storage):
    endpoint = api.Api(storage)
    saved = endpoint.route("POST", "/api/v1/prompt-presets", {}, {"presets": [
        {"name": "Tests", "text": "Run the tests", "send_now": True}, {"name": "Commit", "text": "Commit it"}]})[1]
    assert [(p["number"], p["name"], p["send_now"]) for p in saved] == [(1, "Tests", True), (2, "Commit", False)]
    assert _error(lambda: endpoint.save_prompt_presets({"presets": [{"name": "", "text": "x"}]})).status == 400

    endpoint.save_launch_preset({"name": "Review", "options": ["perm_plan", "model_opus"], "remote_control": True})
    preset = endpoint.launch_presets()["presets"][0]
    assert preset["name"] == "Review" and "model_opus" in preset["options"] and preset["remote_control"]
    endpoint.route("POST", "/api/v1/launch-presets/delete", {}, {"name": "Review"})
    assert endpoint.launch_presets()["presets"] == []


def test_backup_now(storage, tmp_path):
    result = api.Api(storage).route("POST", "/api/v1/backup", {}, {})[1]
    assert result["path"].startswith(str(tmp_path / "backups"))


# --- Claude Code's terminal --------------------------------------------------------------


def test_terminal_of_an_interactive_conversation(storage, tmp_path, monkeypatch):
    from ccc import managed_sessions
    from ccc.live_delivery import DeliveryResult
    from ccc.models import DeliveryMode

    folder = tmp_path / "web"
    folder.mkdir()
    q = storage.create_queue("web", str(folder), "s9", delivery_mode=DeliveryMode.INTERACTIVE)
    headless = _queue(storage, tmp_path)
    pressed, pasted = [], []
    monkeypatch.setattr(managed_sessions, "is_running", lambda queue_id: True)
    monkeypatch.setattr(managed_sessions, "screen", lambda queue_id: managed_sessions.Screen("> hi", 80, 24, 4, 0))
    monkeypatch.setattr(managed_sessions, "press", lambda queue_id, keys: pressed.append(keys) or DeliveryResult(True))
    monkeypatch.setattr(managed_sessions, "paste_and_submit",
                        lambda queue_id, text, submit=True: pasted.append((text, submit)) or DeliveryResult(True))
    endpoint = api.Api(storage)

    assert endpoint.route("GET", f"/api/v1/conversations/{q.id}/terminal", {}, {})[1]["screen"] == "> hi"
    endpoint.terminal_input(q.id, {"keys": ["Escape"]})
    endpoint.terminal_input(q.id, {"paste": "/compact", "submit": False})
    assert pressed == [["Escape"]] and pasted == [("/compact", False)]
    assert _error(lambda: endpoint.terminal_input(q.id, {"paste": " "})).status == 400
    assert _error(lambda: endpoint.terminal(headless.id)).status == 409

    monkeypatch.setattr(managed_sessions, "is_running", lambda queue_id: False)
    assert _error(lambda: endpoint.terminal(q.id)).status == 409


def test_live_session_words_follow_the_request_language():
    from ccc import i18n
    from ccc.api_views import _session_word

    token = i18n.use_for_request("cs")
    try:
        assert [_session_word(w) for w in ("interactive", "idle", "blocked", "background")] == [
            "interaktivní", "nečinná", "blokovaná", "na pozadí"]
        assert _session_word("something new") == "something new"  # not known: as Claude Code wrote it
        assert _session_word(None) is None
    finally:
        i18n.end_request(token)
    assert _session_word("idle") == "idle"
