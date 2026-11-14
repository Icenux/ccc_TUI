import json

import pytest

from ccc.search import export_markdown, search_conversations
from ccc.storage import Storage


def _line(kind, text, ts):
    if kind == "user":
        return json.dumps({"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}) + "\n"
    if kind == "tool":
        return json.dumps({"type": "assistant", "timestamp": ts, "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": text}}]}}) + "\n"
    return json.dumps({"type": "assistant", "timestamp": ts, "message": {"content": [{"type": "text", "text": text}]}}) + "\n"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", root)
    (root / "-p").mkdir(parents=True)
    storage = Storage(tmp_path / "h.db")
    a = storage.create_queue("alpha", "/p", "sa")
    b = storage.create_queue("beta", "/p", "sb")
    storage.set_queue_archived(b.id, True)
    (root / "-p" / "sa.jsonl").write_text(
        _line("user", "Fix the LOGIN bug please", "2026-10-13T10:00:00.000Z")
        + _line("tool", "grep -r login src", "2026-10-13T10:00:01.000Z")
        + _line("assistant", "Fixed the login redirect.", "2026-10-13T10:00:02.000Z")
    )
    (root / "-p" / "sb.jsonl").write_text(_line("assistant", "Nothing about that here, only Login page", "2026-10-13T11:00:00.000Z"))
    return storage, a, b, tmp_path


def test_search_is_case_insensitive_across_all_conversations(setup):
    storage, a, b, _ = setup
    hits = search_conversations(storage, "login")
    assert [(h.queue_name, h.kind) for h in hits] == [("beta", "assistant_text"), ("alpha", "assistant_text"), ("alpha", "human")]
    assert "login" in hits[1].snippet.lower()
    assert search_conversations(storage, "   ") == []
    assert search_conversations(storage, "nothing-matches-this") == []


def test_snippet_is_centered_and_short(setup):
    storage, a, *_ = setup
    hit = [h for h in search_conversations(storage, "redirect") if h.queue_id == a.id][0]
    assert len(hit.snippet) <= 130 and "redirect" in hit.snippet


def test_export_markdown(setup):
    storage, a, _, tmp_path = setup
    path = export_markdown(storage, a.id, tmp_path / "exports")
    text = path.read_text()
    assert path.name.startswith("alpha-") and path.suffix == ".md"
    assert text.startswith("# alpha")
    assert "**You**" in text and "Fix the LOGIN bug please" in text
    assert "**Claude**" in text and "Fixed the login redirect." in text
    assert "- ⚙ Bash:" in text


def test_export_markdown_masks_secrets(setup, tmp_path):
    storage, a, _, _ = setup
    root = tmp_path / "projects"
    with (root / "-p" / "sa.jsonl").open("a") as f:
        f.write(_line("assistant", "Set GITHUB_TOKEN=ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "2026-10-13T10:00:03.000Z"))
    text = export_markdown(storage, a.id, tmp_path / "exports").read_text()
    assert "AbCdEfGhIjKlMnOp" not in text and "GITHUB_TOKEN=ghp_[redacted]" in text


def test_search_where_you_choose_and_in_the_tools_claude_ran(setup):
    # #304: a command like grep is only in a tool call; the archive is searched only when asked.
    from ccc.search import ACTIVE, ALL_KINDS, ARCHIVED

    storage, a, b, _ = setup
    assert [(h.queue_name, h.kind) for h in search_conversations(storage, "grep", kinds=ALL_KINDS)] == \
        [("alpha", "tool_call")]
    assert search_conversations(storage, "grep") == []  # messages only (the API's search)
    assert {h.queue_name for h in search_conversations(storage, "login", where=ACTIVE)} == {"alpha"}
    hits = search_conversations(storage, "login", where=ARCHIVED)
    assert [h.queue_name for h in hits] == ["beta"] and hits[0].archived
