"""ccc's notes (ccc.notes) and their part of the HTTP API (ccc.api_notes): paths that stay inside the folder,
versions that refuse a stale edit, a removed note that moves to ccc's own folder."""
import os

import pytest

from ccc import api, notes
from ccc.storage import Storage


@pytest.fixture
def world(tmp_path):
    storage = Storage(str(tmp_path / "ccc.db"))
    return type("World", (), {"api": api.Api(storage), "root": tmp_path / "notes", "tmp": tmp_path})


def call(world, method, path, query=None, body=None):
    return world.api.route(method, "/api/v1" + path, query or {}, body or {})


def error_of(world, *args, **kwargs):
    with pytest.raises(api.ApiError) as raised:
        call(world, *args, **kwargs)
    return raised.value


def make(world, path="ideas/api.md", content="# API\nplan\n"):
    return call(world, "POST", "/notes", body={"path": path, "content": content})[1]


def test_a_note_is_created_read_and_listed(world):
    status, created = call(world, "POST", "/notes", body={"path": "ideas/api.md", "content": "# API ideas\nplan\n"})
    assert status == 201 and created["title"] == "API ideas" and created["content"].startswith("# API")
    assert (world.root / "ideas" / "api.md").read_text() == "# API ideas\nplan\n"
    assert call(world, "GET", "/notes/item", {"path": "ideas/api.md"})[1]["version"] == created["version"]
    listed = call(world, "GET", "/notes")[1]
    assert [n["path"] for n in listed] == ["ideas/api.md"] and "content" not in listed[0]


def test_the_list_can_be_searched_in_names_and_text(world):
    make(world, "a.md", "apples\n")
    make(world, "b.md", "pears\n")
    assert [n["path"] for n in call(world, "GET", "/notes", {"q": "PEAR"})[1]] == ["b.md"]
    assert [n["path"] for n in call(world, "GET", "/notes", {"q": "a.md"})[1]] == ["a.md"]


def test_an_existing_note_is_never_overwritten(world):
    make(world)
    assert error_of(world, "POST", "/notes", body={"path": "ideas/api.md", "content": "x"}).status == 409


def test_a_change_needs_the_version_it_was_read_with(world):
    created = make(world)
    changed = call(world, "POST", "/notes/item", body={"path": "ideas/api.md", "content": "new", "version": created["version"]})[1]
    assert changed["content"] == "new" and changed["version"] != created["version"]
    stale = error_of(world, "POST", "/notes/item", body={"path": "ideas/api.md", "content": "old", "version": created["version"]})
    assert stale.status == 409 and (world.root / "ideas" / "api.md").read_text() == "new"
    assert error_of(world, "POST", "/notes/item", body={"path": "ideas/api.md", "content": "x"}).status == 400
    assert error_of(world, "POST", "/notes/item", body={"path": "no.md", "content": "x", "version": "1-1"}).status == 404


@pytest.mark.parametrize("path", ["../x.md", "/etc/x.md", "a/../../x.md", ".hidden.md", "a/.b/c.md", "x.txt", ".md",
                                  "a\\b.md", "a//b.md", "a/b/c/d/e/f.md", "", "x" * 101 + ".md", "a\nb.md"])
def test_a_path_that_could_leave_the_folder_is_refused(world, path):
    assert error_of(world, "POST", "/notes", body={"path": path, "content": "x"}).status == 400
    assert error_of(world, "GET", "/notes/item", {"path": path}).status == 400
    assert not (world.tmp / "x.md").exists()


def test_a_link_is_never_followed(world):
    make(world, "a.md")
    outside = world.tmp / "secret.md"
    outside.write_text("secret")
    os.symlink(outside, world.root / "link.md")
    os.symlink(world.tmp, world.root / "dir")
    assert [n["path"] for n in call(world, "GET", "/notes")[1]] == ["a.md"]
    assert error_of(world, "GET", "/notes/item", {"path": "link.md"}).status == 400
    assert error_of(world, "POST", "/notes", body={"path": "dir/x.md", "content": "x"}).status == 400
    assert not (world.tmp / "x.md").exists() and outside.read_text() == "secret"


def test_a_note_too_long_is_refused(world):
    assert error_of(world, "POST", "/notes", body={"path": "big.md", "content": "x" * (notes.MAX_BYTES + 1)}).status == 400
    status, empty = call(world, "POST", "/notes", body={"path": "n.md"})  # no text: an empty note, titled by its name
    assert status == 201 and empty["content"] == "" and empty["title"] == "n"


def test_a_note_is_renamed_and_not_onto_another(world):
    make(world, "a.md")
    make(world, "b.md")
    assert call(world, "POST", "/notes/rename", body={"path": "a.md", "to": "dir/c.md"})[1]["path"] == "dir/c.md"
    assert (world.root / "dir" / "c.md").exists() and not (world.root / "a.md").exists()
    assert error_of(world, "POST", "/notes/rename", body={"path": "b.md", "to": "dir/c.md"}).status == 409
    assert error_of(world, "POST", "/notes/rename", body={"path": "ghost.md", "to": "z.md"}).status == 404


def test_a_removed_note_moves_to_ccc_own_folder(world):
    make(world, "ideas/api.md", "keep me")
    assert call(world, "DELETE", "/notes/item", {"path": "ideas/api.md"})[1] == {"ok": True}
    assert error_of(world, "GET", "/notes/item", {"path": "ideas/api.md"}).status == 404
    kept = list((world.tmp / "deleted-notes").iterdir())
    assert len(kept) == 1 and kept[0].read_text() == "keep me" and kept[0].name.endswith("ideas__api.md")
    assert error_of(world, "DELETE", "/notes/item", {"path": "ideas/api.md"}).status == 404


def test_notes_are_private_to_the_owner(world):
    make(world)
    assert (os.stat(world.root / "ideas" / "api.md").st_mode & 0o077) == 0
