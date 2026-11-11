import sqlite3

import pytest
from textual.app import App
from textual.widgets import Button, DataTable, Input, OptionList

from ccc import groups
from ccc.models import Queue, QueueStatus
from ccc.storage import Storage
from ccc.tui.archive import ArchiveScreen
from ccc.tui.conversations import ConversationsScreen
from ccc.tui.groups import GroupModal

REAL_MODE = groups.mode


@pytest.fixture
def grouping(monkeypatch):
    # conftest flattens the list for other tests; here it groups as shipped.
    monkeypatch.setattr("ccc.groups.mode", REAL_MODE)
    groups._projects.clear()


def _repo(path):
    (path / ".git").mkdir(parents=True)
    return path


def _worktree(repo, path, name):
    gitdir = repo / ".git" / "worktrees" / name
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n")
    path.mkdir(parents=True)
    (path / ".git").write_text(f"gitdir: {gitdir}\n")
    return path


def _q(id, path, name=None):
    return Queue(id=id, name=name or f"q{id}", project_path=str(path), session_id=f"s{id}")


# --- ccc.groups -----------------------------------------------------------------


def test_project_root_is_the_repository_a_worktree_belongs_to(tmp_path):
    repo = _repo(tmp_path / "shop")
    (repo / "api" / "src").mkdir(parents=True)
    tree = _worktree(repo, repo / ".claude" / "worktrees" / "fix", "fix")
    outside = tmp_path / "notes"
    outside.mkdir()
    groups._projects.clear()
    assert groups.project_root(str(repo / "api" / "src")) == str(repo)
    assert groups.project_root(str(tree)) == str(repo)
    assert groups.project_root(str(outside)) == str(outside)


def test_grouped_own_groups_first_then_projects_and_same_names_told_apart(tmp_path):
    groups._projects.clear()
    a = _repo(tmp_path / "work" / "app")
    b = _repo(tmp_path / "home" / "app")
    shop = _repo(tmp_path / "shop")
    queues = [_q(1, a), _q(2, b), _q(3, shop), _q(4, shop), _q(5, a)]
    result = groups.grouped(queues, {5: "Urgent"}, groups.BY_PROJECT)
    assert [(g.title, [q.id for q in qs]) for g, qs in result] == [
        ("Urgent", [5]), ("home/app", [2]), ("shop", [3, 4]), ("work/app", [1])]
    assert result[0][0].own == "Urgent" and result[1][0].own is None


def test_own_groups_only_puts_the_rest_under_no_group_and_stays_flat_without_any(tmp_path):
    queues = [_q(1, tmp_path), _q(2, tmp_path)]
    assert groups.grouped(queues, {}, groups.OWN_ONLY) == [(None, queues)]
    result = groups.grouped(queues, {2: "Docs"}, groups.OWN_ONLY)
    assert [(g.key, [q.id for q in qs]) for g, qs in result] == [("own:Docs", [2]), (groups.NO_GROUP, [1])]


def test_storage_groups_rename_delete_and_folding(tmp_path, grouping):
    storage = Storage(tmp_path / "h.db")
    a = storage.create_queue("a", "/tmp/a", "sa")
    b = storage.create_queue("b", "/tmp/b", "sb")
    storage.set_conversation_group(a.id, "Shop")
    storage.set_conversation_group(b.id, "Shop")
    groups.set_collapsed(storage, "own:Shop", True)
    groups.rename(storage, "Shop", "E-shop")
    assert storage.conversation_groups() == {a.id: "E-shop", b.id: "E-shop"}
    assert groups.collapsed(storage) == {"own:E-shop"}
    storage.set_conversation_group(b.id, None)
    storage.delete_queue(a.id)
    assert storage.conversation_groups() == {}
    storage.set_conversation_group(b.id, "Docs")
    groups.delete(storage, "Docs")
    assert storage.conversation_groups() == {}
    assert groups.mode(storage) == groups.BY_PROJECT


def test_old_database_opens_unchanged_and_dropping_the_table_takes_it_back(tmp_path):
    db = tmp_path / "h.db"
    storage = Storage(db)
    q = storage.create_queue("old", "/tmp/old", "s-old")
    storage.add_queue_item(q.id, "task")
    with sqlite3.connect(db) as conn:  # as an older ccc left it
        conn.execute("DROP TABLE conversation_groups")
    storage = Storage(db)
    assert [x.name for x in storage.list_queues()] == ["old"]
    assert [i.prompt_text for i in storage.list_pending_items(q.id)] == ["task"]
    storage.set_conversation_group(q.id, "Mine")
    with sqlite3.connect(db) as conn:  # the way back
        conn.execute("DROP TABLE conversation_groups")
    assert [x.name for x in Storage(db).list_queues()] == ["old"]


# --- the list ----------------------------------------------------------------------


def _host(screen):
    class Host(App):
        async def on_mount(self):
            await self.push_screen(screen)

    return Host()


def _rows(app) -> list[str]:
    table = app.screen.query_one("#conversation-queue-list", DataTable)
    return [str(table.get_row(k)[0]).translate(str.maketrans("◑◓◒", "◐◐◐")) for k in table.rows]  # the turning dot, one frame


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr("ccc.transcript.PROJECTS_ROOT", tmp_path / "projects")
    shop, docs = _repo(tmp_path / "shop"), _repo(tmp_path / "docs")
    storage = Storage(tmp_path / "h.db")
    api = storage.create_queue("api", str(shop), "s1")
    web = storage.create_queue("web", str(shop / "web"), "s2")
    guide = storage.create_queue("guide", str(docs), "s3")
    storage.update_queue_status(web.id, QueueStatus.RUNNING)
    return storage, api, web, guide


@pytest.mark.asyncio
async def test_list_groups_by_project_enter_folds_and_it_survives_the_refresh(tmp_path, monkeypatch, grouping):
    storage, api, web, guide = _setup(tmp_path, monkeypatch)
    app = _host(ConversationsScreen(storage, select_queue_id=guide.id))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert [r.split(" ·")[0].strip() for r in _rows(app)] == [
            "▾ docs (1)", "○ guide", "▾ shop (2)", "○ api", "◐ web"]
        assert _rows(app)[2].endswith("· ◐1")
        table = app.screen.query_one("#conversation-queue-list", DataTable)
        table.focus()

        await pilot.press("ctrl+down")
        assert table.cursor_row == 2
        await pilot.press("enter")
        await pilot.pause()
        assert _rows(app)[2:] == ["▸ shop (2) · ◐1"]  # folded, still says it works
        assert groups.collapsed(storage) == {f"project:{tmp_path / 'shop'}"}
        assert app.screen._current_queue_id == guide.id  # the open one stays

        storage.update_queue_status(web.id, QueueStatus.IDLE)
        await app.screen._refresh_current()
        await pilot.pause()
        assert _rows(app)[2:] == ["▸ shop (2)"]
        assert table.cursor_row == 2  # no jump back to the conversation

        await pilot.press("ctrl+up")
        assert table.cursor_row == 0
        await pilot.press("ctrl+up")  # around the start
        assert table.cursor_row == 2

        # Opening a conversation of a folded group unfolds it.
        app.screen.open_conversation(api.id)
        await pilot.pause()
        assert "○ api" in [r.strip() for r in _rows(app)]
        assert groups.collapsed(storage) == set()


@pytest.mark.asyncio
async def test_a_click_on_a_heading_folds_it(tmp_path, monkeypatch, grouping):
    storage, *_rest = _setup(tmp_path, monkeypatch)
    app = _host(ConversationsScreen(storage))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert _rows(app)[0] == "▾ docs (1)"
        await pilot.click("#conversation-queue-list", offset=(3, 0))
        await pilot.pause()
        assert _rows(app)[0] == "▸ docs (1)"
        await pilot.click("#conversation-queue-list", offset=(3, 0))
        await pilot.pause()
        assert _rows(app)[0] == "▾ docs (1)"


@pytest.mark.asyncio
async def test_m_puts_a_conversation_into_a_new_group_and_renames_and_deletes_it(tmp_path, monkeypatch, grouping):
    storage, api, web, guide = _setup(tmp_path, monkeypatch)
    app = _host(ConversationsScreen(storage, select_queue_id=api.id))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one("#conversation-queue-list").focus()
        await pilot.press("m")
        await pilot.pause()
        assert isinstance(app.screen, GroupModal)
        await pilot.press(*"Urgent", "enter")
        await pilot.pause()
        assert storage.conversation_groups() == {api.id: "Urgent"}
        assert _rows(app)[:2] == ["▾ Urgent (1)", "  ○ api"]
        table = app.screen.query_one("#conversation-queue-list", DataTable)
        assert table.cursor_row == 1  # it followed the conversation

        # Another one into the existing group: picked from the list.
        app.screen.open_conversation(guide.id)
        await pilot.pause()
        table.focus()
        await pilot.press("m")
        await pilot.pause()
        options = app.screen.query_one("#group-options", OptionList)
        assert options.has_focus
        assert options.highlighted == 1  # its current one: none of its own
        await pilot.press("up", "enter")  # Urgent
        await pilot.pause()
        assert storage.conversation_groups() == {api.id: "Urgent", guide.id: "Urgent"}

        # On the heading: rename.
        table.focus()
        table.move_cursor(row=0)
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        name = app.screen.query_one("#group-name", Input)
        name.value = "Today"
        await pilot.press("enter")
        await pilot.pause()
        assert set(storage.conversation_groups().values()) == {"Today"}
        assert _rows(app)[0] == "▾ Today (2)"

        # On a project's heading: nothing to rename.
        table.move_cursor(row=_rows(app).index("▾ shop (1) · ◐1"))
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        assert not isinstance(app.screen, GroupModal)

        # Delete: the conversations go back to their projects.
        table.move_cursor(row=0)
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        await pilot.click("#group-delete")
        await pilot.pause()
        assert storage.conversation_groups() == {}
        assert _rows(app)[0] == "▾ docs (1)"


@pytest.mark.asyncio
async def test_m_takes_it_out_of_its_group(tmp_path, monkeypatch, grouping):
    storage, api, web, guide = _setup(tmp_path, monkeypatch)
    storage.set_conversation_group(api.id, "Urgent")
    app = _host(ConversationsScreen(storage, select_queue_id=api.id))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.screen.query_one("#conversation-queue-list").focus()
        await pilot.press("m", "down", "enter")
        await pilot.pause()
        assert storage.conversation_groups() == {}
        assert "▾ Urgent (1)" not in _rows(app)


@pytest.mark.asyncio
async def test_archive_groups_too_and_enter_on_a_heading_doesnt_restore(tmp_path, monkeypatch, grouping):
    storage, api, web, guide = _setup(tmp_path, monkeypatch)
    for q in (api, web, guide):
        storage.set_queue_archived(q.id, True)
    app = _host(ArchiveScreen(storage))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one("#conversation-queue-list", DataTable)
        table.focus()
        table.move_cursor(row=0)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert _rows(app)[0] == "▸ docs (1)"
        assert storage.get_queue(guide.id).archived


@pytest.mark.asyncio
async def test_settings_switch_to_own_groups_only(tmp_path, monkeypatch, grouping):
    storage, *_rest = _setup(tmp_path, monkeypatch)
    storage.set_setting(groups.MODE_KEY, groups.OWN_ONLY)
    app = _host(ConversationsScreen(storage))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert [r.strip() for r in _rows(app)] == ["○ api", "○ guide", "◐ web"]  # no own group: flat
