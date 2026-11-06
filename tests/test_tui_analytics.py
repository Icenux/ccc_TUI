from datetime import datetime

import pytest
from textual.app import App
from textual.widgets import ProgressBar

from ccc.models import PromptRun, RunStatus
from ccc.storage import Storage
from ccc.tui.analytics import (
    UNAVAILABLE_TEXT,
    AnalyticsScreen,
    build_model_token_bars,
    build_usage_gauges,
)
from ccc.usage_api import ModelUsage, ProUsage, WindowUsage


@pytest.mark.asyncio
async def test_analytics_shows_totals(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: None)

    db_path = str(tmp_path / "history.db")
    storage = Storage(db_path)
    queue = storage.create_queue("proj-d", "/tmp/proj-d", "sess-1")
    storage.insert_prompt_run(
        PromptRun(
            id=0,
            queue_id=queue.id,
            queue_item_id=None,
            prompt_text="x",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            status=RunStatus.SUCCESS,
            input_tokens=100,
            output_tokens=50,
        )
    )

    screen = AnalyticsScreen(storage)

    class _HostApp(App):
        async def on_mount(self):
            await self.push_screen(screen)

    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#analytics-project-table")
        rows = [str(cell) for row_key in table.rows for cell in table.get_row(row_key)]
        assert any("proj-d" in cell for cell in rows)
        assert any("150" in cell for cell in rows)


@pytest.mark.asyncio
async def test_analytics_shows_pro_usage_gauges(tmp_path, monkeypatch):
    fake_usage = ProUsage(
        five_hour=WindowUsage(30.0, datetime(2026, 9, 3, 23, 29).astimezone()),
        seven_day=WindowUsage(47.0, datetime(2026, 9, 4, 12, 59).astimezone()),
        per_model=[ModelUsage("Opus", 62.0, datetime(2026, 9, 4, 12, 59).astimezone())],
    )
    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: fake_usage)

    storage = Storage(tmp_path / "history.db")
    screen = AnalyticsScreen(storage)

    class _HostApp(App):
        async def on_mount(self):
            await self.push_screen(screen)

    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await screen.refresh_pro_usage()
        bars = app.screen.query(ProgressBar)
        assert len(bars) == 3  # 5-hour, weekly, Opus
        percentages = sorted(round(b.percentage * 100) for b in bars)
        assert percentages == [30, 47, 62]

        labels = [str(s.render()) for s in app.screen.query(".usage-gauge-label")]
        assert any("5-hour" in label for label in labels)
        assert any("Weekly (all models)" in label for label in labels)
        assert any("Opus" in label for label in labels)


@pytest.mark.asyncio
async def test_analytics_pro_usage_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: None)

    storage = Storage(tmp_path / "history.db")
    screen = AnalyticsScreen(storage)

    class _HostApp(App):
        async def on_mount(self):
            await self.push_screen(screen)

    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await screen.refresh_pro_usage()
        assert len(app.screen.query(ProgressBar)) == 0
        texts = [str(s.render()) for s in app.screen.query("#pro-usage-gauges Static")]
        assert any(UNAVAILABLE_TEXT in t for t in texts)


@pytest.mark.asyncio
async def test_analytics_shows_model_usage_bars(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: None)

    storage = Storage(tmp_path / "history.db")
    queue = storage.create_queue("proj-e", "/tmp/proj-e", "sess-1")
    run_id = storage.insert_prompt_run(
        PromptRun(
            id=0,
            queue_id=queue.id,
            queue_item_id=None,
            prompt_text="x",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            status=RunStatus.SUCCESS,
        )
    )
    storage.insert_run_model_usage(run_id, "claude-sonnet-5", input_tokens=100, output_tokens=100)
    storage.insert_run_model_usage(run_id, "claude-opus-5", input_tokens=10, output_tokens=10)

    screen = AnalyticsScreen(storage)

    class _HostApp(App):
        async def on_mount(self):
            await self.push_screen(screen)

    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        bars = app.screen.query("#model-usage-gauges ProgressBar")
        assert len(bars) == 2
        percentages = sorted(round(b.percentage * 100) for b in bars)
        assert percentages == [9, 91]  # each model's share of the 220 tokens

        labels = [str(s.render()) for s in app.screen.query("#model-usage-gauges .usage-gauge-label")]
        assert any("claude-sonnet-5" in label and "200 tokens" in label for label in labels)
        assert any("claude-opus-5" in label and "20 tokens" in label for label in labels)


def test_build_model_token_bars_empty():
    assert build_model_token_bars([]) == []


def test_build_model_token_bars_show_share_of_total():
    bars = build_model_token_bars([("claude-sonnet-5", 300), ("claude-opus-5", 100)])
    assert bars[0].share_pct == 75.0
    assert bars[1].share_pct == 25.0


def test_build_model_token_bars_single_model_is_whole_total():
    bars = build_model_token_bars([("claude-opus-5", 42)])
    assert bars[0].share_pct == 100.0


def test_build_usage_gauges_none_returns_empty():
    assert build_usage_gauges(None) == []


def test_build_usage_gauges_with_data():
    usage = ProUsage(
        five_hour=WindowUsage(30.0, datetime(2026, 9, 3, 23, 29).astimezone()),
        seven_day=WindowUsage(47.0, datetime(2026, 9, 4, 12, 59).astimezone()),
        per_model=[ModelUsage("Opus", 62.0, datetime(2026, 9, 4, 12, 59).astimezone())],
    )
    gauges = build_usage_gauges(usage)
    assert [g.label for g in gauges] == ["5-hour", "Weekly (all models)", "Weekly · Opus"]
    assert [g.percentage for g in gauges] == [30.0, 47.0, 62.0]


@pytest.mark.asyncio
async def test_analytics_shows_usage_reserve(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from textual.app import App as _App

    from ccc.tui.analytics import AnalyticsScreen
    from ccc.usage_api import ProUsage, WindowUsage

    usage = ProUsage(WindowUsage(10, datetime.now(timezone.utc) + timedelta(hours=1)), None, [])
    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: usage)
    storage = Storage(tmp_path / "h.db")
    storage.set_setting("usage_reserve", "75")

    class Host(_App):
        async def on_mount(self):
            await self.push_screen(AnalyticsScreen(storage))

    app = Host()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        await pilot.pause()
        assert "75%" in str(app.screen.query_one("#usage-reserve-note").render())


def test_month_series_one_value_per_day_of_month():
    from datetime import date

    from ccc.tui.analytics import month_series

    series = month_series({date(2026, 2, 1): 5, date(2026, 2, 28): 7, date(2026, 3, 1): 9}, 2026, 2)
    assert len(series) == 28 and series[0] == 5 and series[-1] == 7 and sum(series) == 12


@pytest.mark.asyncio
async def test_arrows_switch_calendar_month(tmp_path, monkeypatch):
    from datetime import date, datetime

    from textual.widgets import DataTable

    from ccc.models import PromptRun, RunStatus
    from ccc.token_stats import shift_month
    from ccc.tui.analytics import month_title
    from ccc.tui.app import CCContinueApp

    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: None)
    storage = Storage(tmp_path / "history.db")
    q = storage.create_queue("old-one", str(tmp_path), "s1")
    py, pm = shift_month(date.today().year, date.today().month, -1)
    when = datetime(py, pm, 3, 12, 0)
    storage.insert_prompt_run(PromptRun(id=0, queue_id=q.id, queue_item_id=None, prompt_text="p", started_at=when,
                                        finished_at=when, status=RunStatus.SUCCESS, input_tokens=42))
    storage.delete_queue(q.id)
    app = CCContinueApp(str(tmp_path / "history.db"))
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        screen = app.screen
        assert screen.query_one("#analytics-project-table", DataTable).row_count == 0
        await pilot.press("right")  # no future months
        await pilot.press("left")
        await pilot.pause()
        assert month_title(py, pm) in str(screen.query_one("#month-panel").border_title)
        assert "42 tokens" in str(screen.query_one("#month-panel").border_title)
        row = [str(c) for c in screen.query_one("#analytics-project-table", DataTable).get_row_at(0)]
        assert row[:2] == ["old-one (deleted)", "42"]


@pytest.mark.asyncio
async def test_remote_control_replies_show_in_analytics_and_dashboard(tmp_path, monkeypatch):
    from datetime import date, datetime

    from textual.widgets import DataTable

    from ccc.models import DeliveryMode
    from ccc.session_usage import UsageRecord
    from ccc.tui.app import CCContinueApp

    monkeypatch.setattr("ccc.tui.analytics.get_pro_usage", lambda: None)
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "history.db")
    q = storage.create_queue("phone-chat", str(tmp_path), "sess-rc", delivery_mode=DeliveryMode.REMOTE_CONTROL)
    storage.insert_session_usage(q.id, [UsageRecord("m1", "claude-opus-5-5", datetime.now(), 1, 2, 3, 4)])
    app = CCContinueApp(str(tmp_path / "history.db"))
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        dash = app.screen.query_one("#dashboard-table", DataTable)
        assert "10" in [str(c) for c in dash.get_row_at(0)]
        await pilot.press("a")
        await pilot.pause()
        table = app.screen.query_one("#analytics-project-table", DataTable)
        assert [str(c) for c in table.get_row_at(0)][:2] == ["phone-chat", "10"]
    assert date.today().isoformat() in dict(storage.daily_token_totals())


def test_day_axis_labels_days_under_the_bars():
    from ccc.tui.analytics import day_axis

    wide = day_axis(30, 90)
    assert len(wide) == 90
    assert wide.startswith("1  2  3") and wide.rstrip().endswith("30")
    narrow = day_axis(31, 40)  # too narrow for every day: thinned out
    assert narrow.startswith("1  4") and len(narrow) == 40
    assert day_axis(0, 40) == ""
