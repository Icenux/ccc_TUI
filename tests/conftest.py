import os

import pytest

# Tests run in English whatever the machine's locale or ccc setting (see ccc.i18n).
os.environ["CCC_LANG"] = "en"



@pytest.fixture(autouse=True)
def _no_live_usage_api(monkeypatch):
    # The daemon reads the account's usage (a live HTTPS request) before
    # starting items and after rate limits. Tests must never hit the
    # network: default to "usage unknown"; tests that need figures patch it.
    monkeypatch.setattr("ccc.orchestrator.get_pro_usage", lambda force_refresh=False: None)


@pytest.fixture(autouse=True)
def _no_real_tmux(monkeypatch):
    # Interactive conversations start a real `claude` in tmux (and paste
    # prompts into it) — a test that reached that code once sent a real
    # prompt. Every test fakes managed_sessions / cli_pane at the level it
    # needs; reaching the real tmux fails loudly instead.
    def refuse(args, input_text=None):
        raise RuntimeError(f"test tried to run real tmux: {args}")

    monkeypatch.setattr("ccc.managed_sessions._run", refuse)
    # Tests may run inside tmux: never split the developer's own window.
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)


@pytest.fixture(autouse=True)
def tmux_dir(tmp_path_factory, monkeypatch):
    """ccc's tmux sockets (ccc.tmux) in a directory of the test's own, and
    no servers from before #280: the real ones on this machine must not
    decide which socket a test sees."""
    path = tmp_path_factory.mktemp("ccc-tmux")
    monkeypatch.setenv("CCC_TMUX_DIR", str(path))
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path_factory.mktemp("tmux-tmpdir")))
    monkeypatch.delenv("CCC_TMUX_BIN", raising=False)
    return str(path)


@pytest.fixture(autouse=True)
def _flat_conversation_list(monkeypatch):
    # The list groups conversations by project by default (ccc.groups): each
    # test's tmp folder would get a heading of its own. Tests of other
    # features see the flat list; tests/test_groups.py turns grouping on.
    monkeypatch.setattr("ccc.groups.mode", lambda storage: "own")


@pytest.fixture(autouse=True)
def _ccc_colours(monkeypatch):
    # ccc's views use its $ccc-… colours (ccc.tui.theme), which CCContinueApp
    # hands to Textual; the small host apps of the tests stand in for it.
    from textual.app import App

    from ccc.tui import theme

    monkeypatch.setattr(App, "get_theme_variable_defaults", lambda self: theme.VARIABLES)


@pytest.fixture(autouse=True)
def _no_real_claude_config(tmp_path_factory, monkeypatch):
    # The New conversation window lists the MCP servers of ~/.claude.json
    # (ccc.mcp_servers): tests must neither read the developer's real ones
    # nor depend on them.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-config")))
    monkeypatch.setenv("CCC_MCP_DIR", str(tmp_path_factory.mktemp("ccc-mcp")))
    # Nor the developer's own Claude accounts (ccc.profiles).
    monkeypatch.setenv("CCC_PROFILES_FILE", str(tmp_path_factory.mktemp("ccc-profiles") / "profiles.json"))
