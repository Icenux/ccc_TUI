from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from rich.text import Text
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import DataTable, ProgressBar, Sparkline, Static

from ccc import keymap
from ccc import i18n
from ccc.i18n import N_, _
from ccc.storage import Storage
from ccc.token_stats import month_bounds, month_stats, shift_month
from ccc.tui.layout import SidebarScreen, panel
from ccc.usage_api import ProUsage, get_pro_usage

UNAVAILABLE_TEXT = N_("Claude Pro usage: unavailable (offline, or not logged in via claude.ai OAuth)")


@dataclass
class UsageGauge:
    label: str
    percentage: float  # 0-100
    reset_text: str


def _format_reset(dt: datetime) -> str:
    local_reset = dt.astimezone()
    now = datetime.now().astimezone()
    if local_reset.date() == now.date():
        return f"{local_reset:%H:%M}"
    return f"{i18n.weekday_short(local_reset)} {local_reset:%H:%M}"


def build_usage_gauges(usage: ProUsage | None) -> list[UsageGauge]:
    """One gauge per limit window the account actually exposes: always
    5-hour + weekly (combined across models), plus one more per model IF
    the plan populates model-scoped weekly limits (most Pro accounts
    don't — see usage_api.KNOWN_MODEL_KEYS)."""
    if usage is None:
        return []
    gauges = []
    if usage.five_hour:
        gauges.append(UsageGauge(_("5-hour"), usage.five_hour.utilization_pct, _format_reset(usage.five_hour.resets_at)))
    if usage.seven_day:
        gauges.append(UsageGauge(_("Weekly (all models)"), usage.seven_day.utilization_pct, _format_reset(usage.seven_day.resets_at)))
    for m in usage.per_model:
        gauges.append(UsageGauge(_("Weekly · {model}").format(model=m.model), m.utilization_pct, _format_reset(m.resets_at)))
    return gauges


@dataclass
class ModelTokenBar:
    model: str
    tokens: int
    share_pct: float  # 0-100, this model's share of all tokens in the period


def build_model_token_bars(totals: list[tuple[str, int]]) -> list[ModelTokenBar]:
    """Bars comparing token volume across models ccc has actually
    run — its own recorded history, not an Anthropic account limit (which
    doesn't break down by model on every plan; see build_usage_gauges).
    Each bar is the model's share of the period's total, so the bars add
    up to 100%."""
    if not totals:
        return []
    total = sum(tokens for _model, tokens in totals) or 1
    return [ModelTokenBar(model, tokens, (tokens / total) * 100) for model, tokens in totals]


def month_series(by_day: dict[date, int], year: int, month: int) -> list[int]:
    """One value per day of the calendar month, 0 for days without usage —
    otherwise a single busy day would stretch over the whole chart."""
    start, end = month_bounds(year, month)
    return [by_day.get(start + timedelta(days=n), 0) for n in range((end - start).days)]


def month_title(year: int, month: int) -> str:
    return i18n.month_title(year, month)


def day_axis(days: int, width: int) -> str:
    """Day numbers for the sparkline, aligned under the bars: the sparkline
    stretches `days` values over `width` columns, so day N starts at column
    (N-1) * width / days. Labels are thinned out (every k-th day) when the
    columns are too narrow for all of them."""
    if days <= 0 or width <= 0:
        return ""
    cell = width / days
    step = 1 if cell >= 3 else math.ceil(3 / cell)
    line = [" "] * width
    for day in range(1, days + 1, step):
        label = str(day)
        start = int((day - 1) * cell)
        if start + len(label) > width:
            break
        line[start:start + len(label)] = label
    return "".join(line)


class AnalyticsScreen(SidebarScreen):
    VIEW = "analytics"
    SUB_TITLE = _("Analytics")

    BINDINGS = [
        keymap.binding("screen.back", "back", _("Back"), show=False),
        # priority: the tables would otherwise take the arrows.
        keymap.binding("analytics.prev", "month(-1)", _("Prev month"), priority=True),
        keymap.binding("analytics.next", "month(1)", _("Next month"), priority=True),
    ]

    DEFAULT_CSS = """
    AnalyticsScreen #analytics-scroll {
        height: 1fr;
    }
    AnalyticsScreen #pro-usage-gauges, AnalyticsScreen #model-usage-gauges {
        height: auto;
    }
    AnalyticsScreen .usage-gauge-row {
        height: 1;
        margin-bottom: 1;
    }
    AnalyticsScreen .usage-gauge-row:last-child {
        margin-bottom: 0;
    }
    AnalyticsScreen .usage-gauge-label {
        width: 48;
    }
    AnalyticsScreen .usage-gauge-row ProgressBar {
        width: 1fr;
    }
    AnalyticsScreen #tokens-sparkline {
        height: 3;
    }
    AnalyticsScreen #analytics-summary {
        margin-top: 1;
    }
    """

    def __init__(self, storage: Storage):
        super().__init__(storage)
        today = date.today()
        self.year, self.month = today.year, today.month

    def compose_main(self):
        yield VerticalScroll(
            panel(Container(id="pro-usage-gauges"), title=_("Claude Pro usage (account limits)")),
            panel(
                Sparkline([], id="tokens-sparkline"),
                Static(id="tokens-day-axis"),
                title=_("Tokens per day"),
                id="month-panel",
            ),
            panel(Container(id="model-usage-gauges"), title=_("Tokens by model"), id="model-panel"),
            panel(
                DataTable(id="analytics-project-table"),
                Static(id="analytics-summary"),
                title=_("Tokens per conversation"),
                id="project-panel",
            ),
            id="analytics-scroll",
        )

    async def on_mount(self) -> None:
        self.run_worker(self.refresh_pro_usage(), exclusive=True)
        self.set_interval(30.0, lambda: self.run_worker(self.refresh_pro_usage(), exclusive=True))

        table = self.query_one("#analytics-project-table", DataTable)
        table.cursor_type = "row"
        table.add_columns(_("Conversation"), _("Tokens"), _("Success"), _("Errors"), _("Rate-limited"))
        await self._render_month()

        blocked_seconds = self.storage.blocked_seconds_total()
        summary = self.query_one("#analytics-summary", Static)
        summary.update(_("Total time blocked on rate limits: [b]{minutes} min[/b]").format(minutes=f"{blocked_seconds / 60:.1f}"))

    async def refresh_pro_usage(self) -> None:
        usage = await asyncio.to_thread(get_pro_usage)
        container = self.query_one("#pro-usage-gauges", Container)
        await container.remove_children()

        gauges = build_usage_gauges(usage)
        if not gauges:
            await container.mount(Static(_(UNAVAILABLE_TEXT)))
            return

        for gauge in gauges:
            await self._mount_gauge_row(container, gauge.label, gauge.percentage, _("resets {time}").format(time=gauge.reset_text))

        from ccc.orchestrator import usage_reserve

        reserve = usage_reserve(self.storage)
        note = (
            _("Your reserve: queued tasks wait while a window is at or above {reserve}%  (change in Settings, Ctrl+P)").format(
                reserve=reserve)
            if reserve
            else _("No usage reserve — queued tasks may use the whole limit  (set one in Settings, Ctrl+P)")
        )
        await container.mount(Static(note, id="usage-reserve-note", classes="muted"))

    async def action_month(self, step: int) -> None:
        year, month = shift_month(self.year, self.month, step)
        today = date.today()
        if (year, month) > (today.year, today.month):
            return  # nothing in the future
        self.year, self.month = year, month
        await self._render_month()

    def _render_day_axis(self) -> None:
        days = getattr(self, "_days_in_month", 0)
        sparkline = self.query_one("#tokens-sparkline", Sparkline)
        self.query_one("#tokens-day-axis", Static).update(day_axis(days, sparkline.size.width))

    def on_resize(self) -> None:
        # The sparkline gets its width only after layout.
        self.call_after_refresh(self._render_day_axis)

    async def _render_month(self) -> None:
        """Chart, per-model bars and per-conversation table of the selected
        calendar month (headless runs + remote-control replies, deleted
        conversations included)."""
        stats = month_stats(self.storage, self.year, self.month)
        title = month_title(self.year, self.month)
        self.query_one("#month-panel").border_title = (
            _("Tokens per day — {month}: {tokens} tokens  (← → month)").format(month=title, tokens=f"{stats.total:,}")
        )
        self.query_one("#model-panel").border_title = _("Tokens by model — {month}").format(month=title)
        self.query_one("#project-panel").border_title = _("Tokens per conversation — {month}").format(month=title)
        series = month_series(stats.by_day, self.year, self.month)
        self.query_one("#tokens-sparkline", Sparkline).data = series
        self._days_in_month = len(series)
        self._render_day_axis()

        table = self.query_one("#analytics-project-table", DataTable)
        table.clear()
        for c in stats.by_conversation:
            name = Text(c.name + (_(" (deleted)") if c.deleted else ""), style="dim" if c.deleted else "")
            table.add_row(name, f"{c.tokens:,}", str(c.success), str(c.errors), str(c.rate_limited))

        container = self.query_one("#model-usage-gauges", Container)
        await container.remove_children()
        await self._mount_model_usage_bars(stats.by_model)

    async def _mount_model_usage_bars(self, totals: list[tuple[str, int]]) -> None:
        container = self.query_one("#model-usage-gauges", Container)
        bars = build_model_token_bars(totals)
        if not bars:
            await container.mount(Static(_("No tokens recorded in this month.")))
            return
        for bar in bars:
            await self._mount_gauge_row(container, bar.model, bar.share_pct, _("{tokens} tokens").format(tokens=f"{bar.tokens:,}"))

    async def _mount_gauge_row(self, container: Container, label: str, percentage: float, suffix: str) -> None:
        progress_bar = ProgressBar(total=100, show_eta=False)
        row = Horizontal(
            Static(f"{label} ({suffix})", classes="usage-gauge-label"),
            progress_bar,
            classes="usage-gauge-row",
        )
        await container.mount(row)
        progress_bar.update(progress=percentage)
