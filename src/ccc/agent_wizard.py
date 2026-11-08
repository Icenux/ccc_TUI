"""A new agent from the user's own description (#220), without knowing what a
`tools:` list or a model alias is: three plain questions become the file's
fields, and the prompt starts as a short outline or is written by Claude.

Claude writes only the prompt text, in a fresh session that cannot read, write or run
anything (`--tools ""`) and is not saved. What it returns lands in the window to be read
and changed before anything is written to disk."""
from __future__ import annotations

import asyncio
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ccc import i18n
from ccc.i18n import N_, _
from ccc.runner import RunResult, classify_output
from ccc.models import RunStatus

MAX_PROMPT_CHARS = 8000
GENERATE_MODEL = "sonnet"
READ_TOOLS = ("Read", "Grep", "Glob")


@dataclass(frozen=True)
class Choice:
    key: str
    label: str  # English, marked with N_(): shown through _()
    tools: tuple[str, ...] = ()
    model: str = ""


# "May it change anything?" — the least that works comes first (and is the default).
ACCESS: tuple[Choice, ...] = (
    Choice("read", N_("Only read and search (safe)"), READ_TOOLS),
    Choice("run", N_("Also run commands (tests, git …)"), (*READ_TOOLS, "Bash")),
    Choice("edit", N_("Also change files"), (*READ_TOOLS, "Bash", "Edit", "Write")),
)
# "How thorough?" — a cheaper model where the job allows.
EFFORT: tuple[Choice, ...] = (
    Choice("fast", N_("Fast and cheap (Haiku)"), model="haiku"),
    Choice("balanced", N_("Balanced (Sonnet)"), model="sonnet"),
    Choice("thorough", N_("The most capable (Opus)"), model="opus"),
)
DEFAULT_ACCESS = "read"
DEFAULT_EFFORT = "balanced"
BY_ACCESS = {c.key: c for c in ACCESS}
BY_EFFORT = {c.key: c for c in EFFORT}


def outline_prompt(job: str, access: str) -> str:
    """A short, safe starting prompt when Claude is not asked to write one."""
    text = _("You are a specialist helper. Your job: {job}\n\n"
             "Work step by step, stay within this job and report briefly what you found or did. "
             "If a task does not fit this job, say so in one sentence instead of improvising.").format(
                 job=" ".join(job.split()) or _("(describe the job here)"))
    if access == "read":
        text += "\n\n" + _("You only read: never change a file.")
    return text


def generation_request(job: str, tools: tuple[str, ...]) -> str:
    """What Claude is asked: the system prompt of a subagent, nothing else."""
    language = "Czech" if i18n.language() == "cs" else "English"  # said to Claude, not shown
    return (
        "Write the system prompt of a Claude Code subagent. Answer with the prompt text only: "
        "no title, no explanation, no code fences.\n\n"
        f"The agent's job, as its user described it: {' '.join(job.split())}\n"
        f"Tools it has: {', '.join(tools)}.\n\n"
        "Rules for the prompt:\n"
        "- Address the agent as \"you\"; one job, stated in the first sentence.\n"
        "- Concrete steps, and exactly what it should report back (short: its answer is a summary for another agent).\n"
        "- Say what it must not do; if it has no Edit or Write tool, say it never changes files.\n"
        "- At most 200 words, Markdown allowed.\n"
        f"- Write it in {language}.")


def clean(text: str) -> str:
    """Claude's answer as a prompt: surrounding code fence and whitespace removed."""
    text = (text or "").strip()
    fenced = re.fullmatch(r"```[\w-]*\n(.*?)\n```", text, re.S)
    return (fenced.group(1) if fenced else text).strip()[:MAX_PROMPT_CHARS]


def command(request: str) -> list[str]:
    """`claude -p` with no tools at all and no saved session."""
    return ["claude", "-p", request, "--output-format", "json", "--no-session-persistence", "--tools", "",
            "--permission-mode", "dontAsk", "--setting-sources", "user", "--model", GENERATE_MODEL]


async def _run(request: str) -> RunResult:
    with tempfile.TemporaryDirectory() as folder:  # not a project: nothing of one is read
        try:
            proc = await asyncio.create_subprocess_exec(
                *command(request), cwd=folder, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as error:
            return RunResult(status=RunStatus.ERROR, error_text=str(error))
        stdout, stderr = await proc.communicate()
    return classify_output(stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"),
                           proc.returncode or 0)


async def generate_prompt(job: str, tools: tuple[str, ...],
                          run_fn: Callable[[str], Awaitable[RunResult]] = _run) -> str:
    """The prompt Claude wrote for the job. ValueError (a message for the user) when it could not."""
    if not job.strip():
        raise ValueError(_("Describe what the agent is for first."))
    result = await run_fn(generation_request(job, tools))
    if result.status != RunStatus.SUCCESS:
        raise ValueError((result.error_text or _("Claude did not answer.")).strip()[:300])
    prompt = clean(result.result_text or "")
    if not prompt:
        raise ValueError(_("Claude did not answer."))
    return prompt
