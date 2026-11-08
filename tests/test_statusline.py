import json

import pytest

from ccc import statusline
from ccc.storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(str(tmp_path / "ccc.db"))


def _input(session="s1", percent=42.5, tokens=85000, window=200000):
    return json.dumps({"session_id": session, "context_window": {
        "used_percentage": percent, "total_input_tokens": tokens, "context_window_size": window}})


def test_parse_reads_the_context_of_a_session():
    assert statusline.parse(_input()) == ("s1", 42.5, 85000, 200000)


def test_parse_ignores_input_without_a_fill():
    assert statusline.parse(_input(percent=None)) is None  # no messages yet
    assert statusline.parse('{"session_id": "s1"}') is None
    assert statusline.parse("not json") is None
    assert statusline.parse("") is None


def test_run_stores_the_context_with_the_conversation(storage, tmp_path, capsys):
    q = storage.create_queue("shop", str(tmp_path), "s1")

    assert statusline.run(storage.db_path, _input()) == 0

    found = storage.find_queue(q.id)
    assert (found.context_percent, found.context_tokens, found.context_window) == (42.5, 85000, 200000)
    assert capsys.readouterr().out == "ctx 42 %\n"


def test_run_ignores_an_unknown_session(storage, tmp_path):
    q = storage.create_queue("shop", str(tmp_path), "s1")
    assert statusline.run(storage.db_path, _input(session="other")) == 0
    assert storage.find_queue(q.id).context_percent is None


def test_run_hands_over_to_the_users_own_status_line(storage, tmp_path, capsys):
    storage.create_queue("shop", str(tmp_path), "s1")

    statusline.run(storage.db_path, _input(), then="python3 -c \"import sys,json; print('mine', json.load(sys.stdin)['session_id'])\"")

    assert capsys.readouterr().out == "mine s1\n"


def test_run_never_fails_on_garbage(tmp_path):
    assert statusline.run(str(tmp_path / "missing.db"), "garbage", then="false") == 0
