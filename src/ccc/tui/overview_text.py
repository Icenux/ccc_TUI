"""The texts of the queue detail's four boxes — Now, Changes, Usage, Errors —
from ccc.overview's data. Arbitrary text (file names, errors, what Claude
said) goes in as plain Content, never as markup."""
from __future__ import annotations

from datetime import datetime

from textual.content import Content

from ccc.i18n import _

SPARK = "▁▂▃▄▅▆▇█"
FILES_SHOWN = 60

def kind_label(kind: str) -> str:
    return {
        "human": _("you"),
        "assistant_text": "Claude",
        "tool_call": _("tool"),
        "tool_result": _("result"),
    }.get(kind, kind)

STATE_STYLES = {
    "working": "bold green",
    "waiting_approval": "bold yellow",
    "waiting_limit": "yellow",
    "paused_limit": "yellow",
    "blocked": "bold red",
    "paused": "dim",
    "idle": "",
}


def when(value: str | None) -> str:
    """HH:MM for today, else the date and time."""
    if not value:
        return "—"
    moment = datetime.fromisoformat(value)
    return f"{moment:%H:%M}" if moment.date() == datetime.now().date() else f"{moment:%Y-%m-%d %H:%M}"


def sparkline(values: list[int]) -> str:
    top = max(values, default=0)
    if top <= 0:
        return SPARK[0] * len(values)
    return "".join(SPARK[min(len(SPARK) - 1, round(v / top * (len(SPARK) - 1)))] if v else SPARK[0] for v in values)


def now_lines(now: dict) -> list[Content]:
    lines = [Content.assemble(
        (now["state_label"], STATE_STYLES.get(now["state"], "")),
        ("   ·   " + _("Queued messages: {n}").format(n=now["pending"]), ""),
    )]
    if now.get("next_retry_at"):
        lines.append(Content.assemble((_("Next retry: ") + when(now["next_retry_at"]), "yellow")))
    event = now.get("last_event")
    if event:
        who = kind_label(event["kind"])
        lines.append(Content.assemble(
            (_("Last activity: ") + when(event["time"]) + "  ", "dim"), (who + ": ", "bold"), Content(event["text"])))
    else:
        lines.append(Content.assemble((_("No activity yet."), "dim")))
    context = now.get("context")
    if context:
        lines.append(Content(_("Context window: {percent:.0f} % full").format(percent=context["percent"])))
    return lines


def changes_lines(changes: dict | None) -> list[Content]:
    if changes is None:
        return [Content.assemble((_("Not shown: ccc reads git only in a git folder, in conversations started with "
                                    "“Bypass permissions”."), "dim"))]
    head = [changes["branch"] or "—"]
    if changes["added"] or changes["removed"]:
        head.append(f"+{changes['added']} −{changes['removed']}")
    if changes["ahead"]:
        head.append(f"↑{changes['ahead']}")
    if changes["behind"]:
        head.append(f"↓{changes['behind']}")
    lines = [Content.assemble((_("Branch: "), "bold"), Content("  ·  ".join(head)))]
    files = changes["files"]
    if not files:
        lines.append(Content.assemble((_("No uncommitted changes."), "dim")))
    for item in files[:FILES_SHOWN]:
        if item["status"] == "new":
            stat = _("new")
        elif item["added"] is None:
            stat = _("binary")
        else:
            stat = f"+{item['added']} −{item['removed']}"
        lines.append(Content.assemble((f"{stat:<12}", "dim"), Content(item["path"])))
    more = len(files) - FILES_SHOWN
    if more > 0 or changes.get("truncated"):
        lines.append(Content.assemble((_("… and more files"), "dim")))
    return lines


def usage_lines(usage: dict) -> list[Content]:
    days = usage["days"]
    lines = [
        Content.assemble((_("Last {n} days: ").format(n=len(days)), "bold"), Content(sparkline([d["tokens"] for d in days]))),
        Content(_("Today: {today} tokens   ·   All time: {total} tokens").format(
            today=f"{usage['today']:,}", total=f"{usage['total']:,}")),
    ]
    if usage["models"]:
        lines.append(Content(_("Models: ") + "  ·  ".join(f"{m['model']} {m['tokens']:,}" for m in usage["models"])))
    return lines


def errors_lines(errors: dict) -> list[Content]:
    lines = []
    if errors["current"]:
        lines.append(Content.assemble((_("Now: "), "bold red"), Content(errors["current"][:200])))
    if not errors["recent"]:
        lines.append(Content.assemble((_("No failed runs."), "dim")))
        return lines
    lines.append(Content.assemble((_("{n} failed run(s) in the last {days} days").format(
        n=errors["count"], days=errors["last_days"]), "dim")))
    for run in errors["recent"]:
        lines.append(Content.assemble(
            (when(run["time"]) + "  ", "dim"), (run["status"] + "  ", "red"), Content(run["text"].replace("\n", " ")[:160])))
    return lines


def joined(lines: list[Content]) -> Content:
    return Content("\n").join(lines)
