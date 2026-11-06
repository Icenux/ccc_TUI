import json

import pytest
from textual.app import App
from textual.widgets import Button, DataTable, Static

from ccc.storage import Storage
from ccc.tui.conversations import ConversationsScreen


@pytest.mark.asyncio
async def test_shows_queue_list_and_placeholder_for_no_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "projects").mkdir()

    storage = Storage(tmp_path / "history.db")
    storage.create_queue("proj-a", "/tmp/proj-a", "session-aaa")

    screen = ConversationsScreen(storage)

    class _HostApp(App):
        async def on_mount(self):
            await self.push_screen(screen)

    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        table = app.screen.query_one("#conversation-queue-list", DataTable)
        rows = [str(cell) for row_key in table.rows for cell in table.get_row(row_key)]
        assert any("proj-a" in cell for cell in rows)

        right_panel_text = "\n".join(
            str(s.render()) for s in _shown(app, "Static")
        )
        assert "No conversation yet." in right_panel_text


@pytest.mark.asyncio
async def test_renders_real_conversation_and_switches_on_arrow_key(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    project_dir = tmp_path / "projects" / "-tmp-proj-a"
    project_dir.mkdir(parents=True)

    storage = Storage(tmp_path / "history.db")
    storage.create_queue("proj-a", "/tmp/proj-a", "session-aaa")
    storage.create_queue("proj-b", "/tmp/proj-b", "session-bbb")

    transcript_a = project_dir / "session-aaa.jsonl"
    transcript_a.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": "ahoj z A"},
                "timestamp": "2026-09-04T10:00:00.000Z",
            }
        )
        + "\n"
    )

    screen = ConversationsScreen(storage)

    class _HostApp(App):
        async def on_mount(self):
            await self.push_screen(screen)

    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        def right_panel_text():
            return "\n".join(str(s.render()) for s in _shown(app, "Static"))

        assert "ahoj z A" in right_panel_text()

        await pilot.press("down")
        await pilot.pause()

        assert "No conversation yet." in right_panel_text()
        assert "ahoj z A" not in right_panel_text()


@pytest.mark.asyncio
async def test_picks_up_new_transcript_lines_on_periodic_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    project_dir = tmp_path / "projects" / "-tmp-proj-a"
    project_dir.mkdir(parents=True)

    storage = Storage(tmp_path / "history.db")
    storage.create_queue("proj-a", "/tmp/proj-a", "session-aaa")

    transcript_a = project_dir / "session-aaa.jsonl"
    transcript_a.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "first"}, "timestamp": "2026-09-04T10:00:00.000Z"})
        + "\n"
    )

    screen = ConversationsScreen(storage)

    class _HostApp(App):
        async def on_mount(self):
            await self.push_screen(screen)

    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        with open(transcript_a, "a") as f:
            f.write(
                json.dumps({"type": "user", "message": {"role": "user", "content": "second"}, "timestamp": "2026-09-04T10:01:00.000Z"})
                + "\n"
            )

        # directly invoke the refresh instead of sleeping 2 real seconds
        await screen._refresh_current()
        await pilot.pause()

        right_panel_text = "\n".join(str(s.render()) for s in _shown(app, "Static"))
        assert "first" in right_panel_text
        assert "second" in right_panel_text


@pytest.mark.asyncio
async def test_message_text_containing_markup_like_syntax_renders_literally(tmp_path, monkeypatch):
    # Regression test for a real markup-injection bug: conversation text
    # interpolated unescaped into a Rich markup string had text like
    # "[red]URGENT[/red]" silently interpreted as a style tag instead of
    # shown as the literal characters the human/model actually
    # wrote/said — confirmed with rich.console.Console before the fix.
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    project_dir = tmp_path / "projects" / "-tmp-proj-a"
    project_dir.mkdir(parents=True)

    storage = Storage(tmp_path / "history.db")
    storage.create_queue("proj-a", "/tmp/proj-a", "session-aaa")

    transcript_a = project_dir / "session-aaa.jsonl"
    transcript_a.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": "My note says: [red]URGENT[/red] fix this"},
                "timestamp": "2026-09-04T10:00:00.000Z",
            }
        )
        + "\n"
    )

    screen = ConversationsScreen(storage)

    class _HostApp(App):
        async def on_mount(self):
            await self.push_screen(screen)

    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        right_panel_text = "\n".join(str(s.render()) for s in _shown(app, "Static"))
        assert "[red]URGENT[/red]" in right_panel_text


@pytest.mark.asyncio
async def test_sidebar_buttons_show_full_label_text(tmp_path, monkeypatch):
    # Regression test for the same markup-injection class of bug as
    # message text: Button labels are also parsed as Rich markup, so an
    # earlier draft using "Add prompt [a]" silently lost the "[a]" —
    # square brackets look like a (invalid, silently-dropped) style tag.
    # Fixed by using parentheses instead; this locks that in.
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "projects").mkdir()
    storage = Storage(tmp_path / "history.db")

    screen = ConversationsScreen(storage)

    class _HostApp(App):
        async def on_mount(self):
            await self.push_screen(screen)

    app = _HostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        labels = {str(b.label) for b in app.screen.query(Button)}
        assert "New conversation (n)" in labels
        assert "Retry blocked queue (r)" in labels


from textual.widgets import Input

from ccc.tui.message_box import MessageBox


def _shown(app, selector):
    """Widgets matching `selector` in the transcript on screen — not in the
    ones kept hidden for conversations you switched away from."""
    view = next(v for v in app.screen.query("#conversation-transcript > .transcript-view") if v.display)
    return view.query(selector)


async def _open(storage, **kw):
    screen = ConversationsScreen(storage, **kw)

    class _HostApp(App):
        async def on_mount(self):
            await self.push_screen(screen)

    return _HostApp()


@pytest.mark.asyncio
async def test_message_input_queues_prompt_and_shows_queued_bubble(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "history.db")
    q = storage.create_queue("proj-a", "/tmp/proj-a", "session-aaa", launch_flags=["--model", "haiku"])
    app = await _open(storage)
    async with app.run_test() as pilot:
        await pilot.pause()
        box = app.screen.query_one("#message-input", MessageBox)
        box.focus()
        await pilot.press(*"ahoj")
        await pilot.press("enter")
        await pilot.pause()
        assert [i.prompt_text for i in storage.list_pending_items(q.id)] == ["ahoj"]
        assert box.text == ""
        queued = app.screen.query(".message-box-queued")
        assert len(queued) == 1 and "ahoj" in str(queued.first().render())
        assert "1 queued" in str(app.screen.query_one("#conversation-status").render())
        assert "Haiku" in str(app.screen.query_one("#conversation-header").render())


@pytest.mark.asyncio
async def test_new_queue_appears_and_select_queue_id_opens_it(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "history.db")
    storage.create_queue("a", "/tmp/a", "s-a")
    b = storage.create_queue("b", "/tmp/b", "s-b")
    app = await _open(storage, select_queue_id=b.id)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen._current_queue_id == b.id
        storage.create_queue("c", "/tmp/c", "s-c")
        await app.screen._refresh_current()
        await pilot.pause()
        table = app.screen.query_one("#conversation-queue-list", DataTable)
        assert table.row_count == 3
        assert app.screen._current_queue_id == b.id


@pytest.mark.asyncio
async def test_status_line_shows_waiting_for_reset(tmp_path, monkeypatch):
    from datetime import datetime
    from ccc.models import QueueStatus
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "history.db")
    q = storage.create_queue("a", "/tmp/a", "s-a")
    storage.update_queue_status(q.id, QueueStatus.WAITING_RETRY, next_retry_at=datetime(2026, 10, 12, 15, 0))
    app = await _open(storage)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "15:00" in str(app.screen.query_one("#conversation-status").render())


@pytest.mark.asyncio
async def test_truncated_tool_preview_with_bracket_does_not_crash(tmp_path, monkeypatch):
    # Regression: a tool-call preview is cut at 80 chars, which can leave
    # an unclosed `[data-section=\"quick…` — Textual's markup parser raised
    # MarkupError on it even after rich's escape(), crashing the screen.
    # Real text from a captured transcript.
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    project_dir = tmp_path / "projects" / "-tmp-proj-a"
    project_dir.mkdir(parents=True)
    storage = Storage(tmp_path / "history.db")
    storage.create_queue("proj-a", "/tmp/proj-a", "session-aaa")
    script = "(() => {\n  const el = document.querySelector('[data-section=\"quick-actions\"]'); return el; })()"
    (project_dir / "session-aaa.jsonl").write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "browser_eval", "input": {"script": script}}]},
                "timestamp": "2026-09-04T10:00:00.000Z",
            }
        )
        + "\n"
    )
    app = await _open(storage)
    async with app.run_test() as pilot:
        await pilot.pause()
        text = "\n".join(str(s.render()) for s in _shown(app, "Static"))
        assert "data-section" in text


@pytest.mark.asyncio
async def test_long_transcript_renders_only_newest_events_and_appends_new_ones(tmp_path, monkeypatch):
    from ccc.tui.conversations import MAX_RENDERED_EVENTS

    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    project_dir = tmp_path / "projects" / "-tmp-proj-a"
    project_dir.mkdir(parents=True)
    storage = Storage(tmp_path / "history.db")
    storage.create_queue("proj-a", "/tmp/proj-a", "session-aaa")
    transcript = project_dir / "session-aaa.jsonl"

    def user_line(i):
        return json.dumps(
            {"type": "user", "message": {"role": "user", "content": f"msg {i}"}, "timestamp": "2026-09-04T10:00:00.000Z"}
        ) + "\n"

    total = MAX_RENDERED_EVENTS + 30
    transcript.write_text("".join(user_line(i) for i in range(total)))
    app = await _open(storage)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()  # the older events follow the newest
        await pilot.pause()
        boxes = _shown(app, ".message-box")
        assert len(boxes) == MAX_RENDERED_EVENTS
        assert "30 earlier messages not shown" in "\n".join(
            str(s.render()) for s in _shown(app, "Static")
        )
        with open(transcript, "a") as f:
            f.write(user_line(total))
        await app.screen._refresh_current()
        await pilot.pause()
        boxes = _shown(app, ".message-box")
        assert len(boxes) == MAX_RENDERED_EVENTS + 1
        assert f"msg {total}" in "\n".join(str(w.render()) for w in boxes.last().query(Static))


@pytest.mark.asyncio
async def test_queue_names_are_not_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "history.db")
    storage.create_queue("a-rather-long-queue-name", "/tmp/a", "s-a")
    app = await _open(storage)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#conversation-queue-list", DataTable)
        assert table.columns[next(iter(table.columns))].get_render_width(table) >= len("a-rather-long-queue-name")


@pytest.mark.asyncio
async def test_paused_limit_status_and_r_resumes(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from ccc.models import QueueStatus

    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "history.db")
    q = storage.create_queue("a", "/tmp/a", "s-a", auto_continue=False)
    reset = (datetime.now() + timedelta(hours=1)).replace(second=0, microsecond=0)
    storage.update_queue_status(q.id, QueueStatus.PAUSED_LIMIT, next_retry_at=reset)
    app = await _open(storage)
    async with app.run_test() as pilot:
        await pilot.pause()
        status = str(app.screen.query_one("#conversation-status").render())
        assert "Paused at usage limit" in status and reset.strftime("%H:%M") in status
        assert "auto-continue off" in str(app.screen.query_one("#conversation-header").render())
        storage.update_queue_status(q.id, QueueStatus.PAUSED_LIMIT, next_retry_at=datetime.now() - timedelta(minutes=1))
        await app.screen._refresh_current()
        assert "press r to continue" in str(app.screen.query_one("#conversation-status").render())
        app.screen.query_one("#conversation-queue-list").focus()
        await pilot.press("r")
        await pilot.pause()
        assert storage.get_queue(q.id).status == QueueStatus.IDLE


@pytest.mark.asyncio
async def test_message_while_paused_at_limit_resumes_queue(tmp_path, monkeypatch):
    from ccc.models import QueueStatus

    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "history.db")
    q = storage.create_queue("a", "/tmp/a", "s-a")
    storage.update_queue_status(q.id, QueueStatus.PAUSED_LIMIT)
    app = await _open(storage)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#message-input", MessageBox).focus()
        await pilot.press(*"go")
        await pilot.press("enter")
        await pilot.pause()
        assert storage.get_queue(q.id).status == QueueStatus.IDLE


@pytest.mark.asyncio
async def test_paused_queue_status_and_t_opens_tasks(tmp_path, monkeypatch):
    from ccc.tui.tasks import TasksModal

    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "history.db")
    q = storage.create_queue("a", "/tmp/a", "s-a")
    storage.set_queue_paused(q.id, True)
    app = await _open(storage)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert "Queue paused" in str(app.screen.query_one("#conversation-status").render())
        app.screen.query_one("#conversation-queue-list").focus()
        await pilot.press("t")
        await pilot.pause()
        assert isinstance(app.screen, TasksModal)
        await pilot.press("p")
        await pilot.press("escape")
        await pilot.pause()
        assert "Queue paused" not in str(app.screen.query_one("#conversation-status").render())


@pytest.mark.asyncio
async def test_header_shows_remote_control_link(tmp_path, monkeypatch):
    from ccc.models import DeliveryMode

    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "history.db")
    q = storage.create_queue("rc", "/tmp/rc", "s-rc", delivery_mode=DeliveryMode.REMOTE_CONTROL, launch_flags=[])
    app = await _open(storage)
    async with app.run_test(size=(200, 40)) as pilot:
        await pilot.pause()
        assert "remote control: starting…" in str(app.screen.query_one("#conversation-header").render())
        storage.set_remote_control_url(q.id, "https://claude.ai/code/session_XYZ")
        await app.screen._refresh_current()
        assert "https://claude.ai/code/session_XYZ" in str(app.screen.query_one("#conversation-header").render())



@pytest.mark.asyncio
async def test_message_box_ctrl_j_adds_line_enter_sends_all(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "history.db")
    q = storage.create_queue("a", "/tmp/a", "s-a")
    app = await _open(storage)
    async with app.run_test() as pilot:
        await pilot.pause()
        box = app.screen.query_one("#message-input", MessageBox)
        box.focus()
        await pilot.press(*"one")
        await pilot.press("ctrl+j")
        await pilot.press(*"two")
        await pilot.press("shift+enter")
        await pilot.press(*"three")
        assert storage.list_pending_items(q.id) == []
        await pilot.press("enter")
        await pilot.pause()
        assert [i.prompt_text for i in storage.list_pending_items(q.id)] == ["one\ntwo\nthree"]
        assert box.text == ""


@pytest.mark.asyncio
async def test_message_box_ignores_blank(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "history.db")
    q = storage.create_queue("a", "/tmp/a", "s-a")
    app = await _open(storage)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#message-input", MessageBox).focus()
        await pilot.press("ctrl+j", "enter")
        await pilot.pause()
        assert storage.list_pending_items(q.id) == []


@pytest.mark.asyncio
async def test_markdown_reply_and_expandable_tool_with_late_result(tmp_path, monkeypatch):
    from textual.widgets import Collapsible, Markdown

    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    project_dir = tmp_path / "projects" / "-tmp-proj-a"
    project_dir.mkdir(parents=True)
    storage = Storage(tmp_path / "history.db")
    storage.create_queue("proj-a", "/tmp/proj-a", "session-aaa")
    transcript = project_dir / "session-aaa.jsonl"
    transcript.write_text(
        json.dumps({"type": "assistant", "timestamp": "2026-10-13T10:00:00.000Z",
                    "message": {"content": [{"type": "text", "text": "# Title\n\n- item [x](y)"},
                                            {"type": "tool_use", "id": "tu1", "name": "Bash",
                                             "input": {"command": "ls [a-z]*"}}]}}) + "\n"
    )
    app = await _open(storage)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert len(_shown(app, "Markdown")) == 1
        tool = _shown(app, "Collapsible").first(Collapsible)
        assert tool.collapsed and "Bash" in str(tool.title)
        assert "waiting for result" in str(app.screen.query_one(".tool-result").render())
        with open(transcript, "a") as f:
            f.write(json.dumps({"type": "user", "timestamp": "2026-10-13T10:00:01.000Z",
                                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1",
                                                                          "content": "a.txt [b]"}]}}) + "\n")
        await app.screen._refresh_current()
        await pilot.pause()
        assert str(app.screen.query_one(".tool-result").render()) == "a.txt [b]"
        assert len(_shown(app, "Collapsible")) == 1  # filled in, not re-added


@pytest.mark.asyncio
async def test_status_shows_next_scheduled_task(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "history.db")
    q = storage.create_queue("a", "/tmp/a", "s-a")
    at = (datetime.now() + timedelta(hours=3)).replace(second=0, microsecond=0)
    storage.add_queue_item(q.id, "later", not_before=at)
    app = await _open(storage)
    async with app.run_test() as pilot:
        await pilot.pause()
        status = str(app.screen.query_one("#conversation-status").render())
        # Late in the day "+3 h" is tomorrow and the date is shown too.
        assert "Next task at" in status and f"{at:%H:%M}" in status


@pytest.mark.asyncio
async def test_status_line_for_usage_reserve(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from ccc.models import QueueStatus

    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "history.db")
    q = storage.create_queue("a", "/tmp/a", "s-a")
    at = (datetime.now() + timedelta(hours=1)).replace(second=0, microsecond=0)
    storage.update_queue_status(q.id, QueueStatus.WAITING_RETRY, next_retry_at=at, last_error="usage reserve 80%")
    app = await _open(storage)
    async with app.run_test() as pilot:
        await pilot.pause()
        status = str(app.screen.query_one("#conversation-status").render())
        assert "usage reserve (80%)" in status and at.strftime("%H:%M") in status


def test_export_is_labelled_export_conversation():
    # The footer shows the binding description; plain "Export" didn't say what.
    descriptions = {b.id: b.description for b in ConversationsScreen.BINDINGS}
    assert descriptions["conversations.export"] == "Export conversation"


@pytest.mark.asyncio
async def test_list_and_header_show_the_git_status(tmp_path, monkeypatch):
    from ccc import gitops

    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "projects").mkdir()
    asked = []

    def fake(path):
        asked.append(path)
        return gitops.WorkStatus(12, 3, 2, 0, "main")

    monkeypatch.setattr(gitops, "work_status", fake)
    storage = Storage(tmp_path / "history.db")
    wt = storage.create_queue("wt", "/tmp/wt", "s1")  # legacy = skip permissions: git allowed
    storage.create_queue("limited", "/tmp/limited", "s2", launch_flags=["--permission-mode", "acceptEdits"])

    class _HostApp(App):
        async def on_mount(self):
            await self.push_screen(ConversationsScreen(storage, select_queue_id=wt.id))

    app = _HostApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await app.screen._refresh_current()
        await pilot.pause()
        table = app.screen.query_one("#conversation-queue-list", DataTable)
        cells = [str(cell) for row_key in table.rows for cell in table.get_row(row_key)]
        assert any("wt" in c and "+12−3 ↑2" in c for c in cells)
        assert not any("limited" in c and "+12" in c for c in cells)
        assert asked == ["/tmp/wt"]  # never runs git where Claude can't run commands
        header = str(app.screen.query_one("#conversation-header", Static).render())
        assert "+12−3 ↑2" in header and "main" in header


def _filter_host(screen):
    class Host(App):
        async def on_mount(self):
            await self.push_screen(screen)

    return Host()


def _list_names(app) -> list[str]:
    table = app.screen.query_one("#conversation-queue-list", DataTable)
    return [str(table.get_row(k)[0])[2:].split(" ")[0] for k in table.rows]


@pytest.mark.asyncio
async def test_f_filters_the_list_by_name_and_state_esc_cancels(tmp_path, monkeypatch):
    from textual.widgets import Input

    from ccc.models import QueueStatus

    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "h.db")
    storage.create_queue("shop-api", "/tmp/shop", "sa")
    docs = storage.create_queue("Dokumentace", "/tmp/docs", "sb")
    broken = storage.create_queue("infra", "/tmp/infra", "sc")
    storage.update_queue_status(broken.id, QueueStatus.BLOCKED_ERROR)

    app = _filter_host(ConversationsScreen(storage))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#conversation-queue-list").focus()
        await pilot.press("f")
        box = app.screen.query_one("#conversation-filter", Input)
        assert box.has_focus

        await pilot.press(*"dokumentace")
        await pilot.pause()
        assert _list_names(app) == ["Dokumentace"]
        assert app.screen._current_queue_id == docs.id  # follows the first result

        box.value = "x"
        await pilot.pause()
        assert _list_names(app) == ["infra"]

        box.value = "nothing-like-this"
        await pilot.pause()
        assert _list_names(app) == []
        assert app.screen.query_one("#conversation-filter-empty").display

        # Survives the periodic refresh.
        await app.screen._refresh_current()
        assert _list_names(app) == []

        await pilot.press("escape")
        await pilot.pause()
        assert len(_list_names(app)) == 3
        assert not app.screen.query_one("#conversation-filter-empty").display
        assert not app.screen.has_class("-filter-open")


@pytest.mark.asyncio
async def test_filter_enter_opens_the_first_result_which_stays_listed(tmp_path, monkeypatch):
    from textual.widgets import Input

    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    storage = Storage(tmp_path / "h.db")
    storage.create_queue("a", "/tmp/a", "sa")
    b = storage.create_queue("b", "/tmp/b", "sb")
    storage.add_queue_item(b.id, "x")
    from datetime import datetime

    from ccc.models import PromptRun, RunStatus

    now = datetime.now()
    storage.insert_prompt_run(PromptRun(id=0, queue_id=b.id, queue_item_id=None, prompt_text="p",
                                        started_at=now, finished_at=now, status=RunStatus.SUCCESS))
    assert b.id in storage.unseen_queue_ids()

    app = _filter_host(ConversationsScreen(storage))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#conversation-queue-list").focus()
        await pilot.press("f", "asterisk")
        await pilot.pause()
        assert _list_names(app) == ["b"]
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen._current_queue_id == b.id
        assert app.screen.query_one("#message-input").has_focus
        # Looked at: no longer new, but stays listed until the filter changes.
        await app.screen._refresh_current()
        assert _list_names(app) == ["b"]
        assert app.screen.query_one("#conversation-filter", Input).value == "*"


@pytest.mark.asyncio
async def test_switching_back_reuses_the_rendered_transcript_and_catches_up(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    project_dir = tmp_path / "projects" / "-tmp-proj-a"
    project_dir.mkdir(parents=True)
    storage = Storage(tmp_path / "history.db")
    qa = storage.create_queue("proj-a", "/tmp/proj-a", "session-aaa")
    qb = storage.create_queue("proj-b", "/tmp/proj-a", "session-bbb")

    def user_line(text):
        return json.dumps(
            {"type": "user", "message": {"role": "user", "content": text}, "timestamp": "2026-09-04T10:00:00.000Z"}
        ) + "\n"

    (project_dir / "session-aaa.jsonl").write_text(user_line("hello from a"))
    (project_dir / "session-bbb.jsonl").write_text(user_line("hello from b"))
    app = await _open(storage)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        view_a = screen._views[qa.id].container
        await screen._show_queue(qb.id)
        await pilot.pause()
        assert not view_a.display
        assert "hello from b" in "\n".join(str(s.render()) for s in _shown(app, "Static"))

        with open(project_dir / "session-aaa.jsonl", "a") as f:
            f.write(user_line("more from a"))
        await screen._show_queue(qa.id)
        await pilot.pause()
        assert screen._views[qa.id].container is view_a  # not mounted again
        assert view_a.display
        text = "\n".join(str(s.render()) for s in _shown(app, "Static"))
        assert "hello from a" in text and "more from a" in text and "hello from b" not in text


@pytest.mark.asyncio
async def test_only_the_latest_views_are_kept(tmp_path, monkeypatch):
    from ccc.tui.conversations import VIEWS_KEPT

    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "projects").mkdir()
    storage = Storage(tmp_path / "history.db")
    queues = [storage.create_queue(f"proj-{i}", "/tmp/proj", f"session-{i}") for i in range(VIEWS_KEPT + 2)]
    app = await _open(storage)
    async with app.run_test() as pilot:
        await pilot.pause()
        for q in queues:
            await app.screen._show_queue(q.id)
        await pilot.pause()
        assert len(app.screen._views) == VIEWS_KEPT
        assert len(app.screen.query("#conversation-transcript > .transcript-view")) == VIEWS_KEPT
        assert queues[0].id not in app.screen._views


@pytest.mark.asyncio
async def test_a_changed_label_updates_the_cell_without_rebuilding_the_list(tmp_path, monkeypatch):
    # A rebuild (clear + add_row) resets the list's hover, so it flickered
    # under the mouse whenever a git status or context fill changed.
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "projects").mkdir()
    storage = Storage(tmp_path / "history.db")
    a = storage.create_queue("proj-a", "/tmp/proj-a", "s1")
    storage.create_queue("proj-b", "/tmp/proj-b", "s2")
    screen = ConversationsScreen(storage, select_queue_id=a.id)

    class _HostApp(App):
        async def on_mount(self):
            await self.push_screen(screen)

    app = _HostApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        table = app.screen.query_one("#conversation-queue-list", DataTable)
        rebuilt = []
        original = screen._rebuild_queue_list
        monkeypatch.setattr(screen, "_rebuild_queue_list", lambda: (rebuilt.append(1), original())[1])

        from ccc import gitops
        screen._git_status = {a.id: gitops.WorkStatus(5, 1, 0, 0, "main")}
        await screen._refresh_current()
        await pilot.pause()
        cells = [str(cell) for row_key in table.rows for cell in table.get_row(row_key)]
        assert any("proj-a" in c and "+5−1" in c for c in cells)
        assert rebuilt == []

        storage.create_queue("proj-c", "/tmp/proj-c", "s3")  # a new row still rebuilds
        await screen._refresh_current()
        assert rebuilt == [1]
