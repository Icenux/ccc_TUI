"""The wizard's pure parts (ccc.agent_wizard): choices, outline, the request to Claude, its answer."""
import pytest

from ccc import agent_wizard as w
from ccc.models import RunStatus
from ccc.runner import RunResult


def test_choices_give_the_least_that_works_by_default():
    assert w.BY_ACCESS[w.DEFAULT_ACCESS].tools == ("Read", "Grep", "Glob")
    assert "Edit" not in w.BY_ACCESS["run"].tools and "Bash" in w.BY_ACCESS["run"].tools
    assert {"Edit", "Write"} <= set(w.BY_ACCESS["edit"].tools)
    assert all("Agent" not in c.tools for c in w.ACCESS)
    assert [w.BY_EFFORT[k].model for k in ("fast", "balanced", "thorough")] == ["haiku", "sonnet", "opus"]


def test_outline_states_the_job_and_read_only():
    text = w.outline_prompt("  Check\nthe texts ", "read")
    assert "Your job: Check the texts" in text and "never change a file" in text
    assert "never change a file" not in w.outline_prompt("x", "edit")
    assert "describe the job" in w.outline_prompt("", "read")


def test_the_request_names_job_tools_and_language():
    from ccc import i18n

    text = w.generation_request("Check texts", ("Read", "Grep"))
    assert "Check texts" in text and "Read, Grep" in text and "English" in text
    i18n.set_language("cs")
    try:
        assert "in Czech" in w.generation_request("x", ("Read",))
    finally:
        i18n.set_language("en")


def test_command_gives_claude_no_tools_and_no_saved_session():
    cmd = w.command("hello")
    assert cmd[cmd.index("--tools") + 1] == "" and "--no-session-persistence" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"


@pytest.mark.parametrize("raw, expected", [
    ("  plain  ", "plain"),
    ("```\nin a fence\n```", "in a fence"),
    ("```markdown\nwith lang\n```", "with lang"),
    ("text with ``` inside", "text with ``` inside"),
    ("", ""),
])
def test_clean(raw, expected):
    assert w.clean(raw) == expected


def test_clean_caps_the_length():
    assert len(w.clean("x" * (w.MAX_PROMPT_CHARS + 50))) == w.MAX_PROMPT_CHARS


@pytest.mark.asyncio
async def test_generate_returns_claudes_prompt():
    asked = []

    async def ok(request):
        asked.append(request)
        return RunResult(status=RunStatus.SUCCESS, result_text="```\nYou are a checker.\n```")

    assert await w.generate_prompt("Check texts", ("Read",), ok) == "You are a checker."
    assert "Check texts" in asked[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("result, message", [
    (RunResult(status=RunStatus.ERROR, error_text="boom"), "boom"),
    (RunResult(status=RunStatus.ERROR), "did not answer"),
    (RunResult(status=RunStatus.SUCCESS, result_text="  "), "did not answer"),
])
async def test_generate_reports_failures_as_a_message(result, message):
    async def fake(request):
        return result

    with pytest.raises(ValueError, match=message):
        await w.generate_prompt("job", ("Read",), fake)


@pytest.mark.asyncio
async def test_generate_needs_a_job_and_does_not_call_claude_without_one():
    async def never(request):
        raise AssertionError("Claude must not be asked")

    with pytest.raises(ValueError):
        await w.generate_prompt("  ", ("Read",), never)
