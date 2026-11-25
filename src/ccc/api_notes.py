"""ccc's notes (ccc.notes, #223) over the HTTP API (see ccc.api): Markdown files shared with the app.

GET    /api/v1/notes?q=                 every note, the newest first, without text: path, title, size, modified, version;
                                        ?q= keeps those with it in the path or the text
POST   /api/v1/notes                    create {"path", "content"}; 201, 409 for an existing path
GET    /api/v1/notes/item?path=         one note with its `content` and `version`
POST   /api/v1/notes/item               change {"path", "content", "version"}; 409 when the note changed since that version
POST   /api/v1/notes/rename             {"path", "to"}
DELETE /api/v1/notes/item?path=         remove (the file moves to ccc's own folder)

The path is always in the query or the body (never in the URL path), so a folder in it needs no escaping."""
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

from ccc import notes
from ccc.api_common import ApiError, route

_STATUS = {notes.BAD: HTTPStatus.BAD_REQUEST, notes.MISSING: HTTPStatus.NOT_FOUND,
           notes.EXISTS: HTTPStatus.CONFLICT, notes.CONFLICT: HTTPStatus.CONFLICT}


def _failed(error: notes.NoteError) -> ApiError:
    return ApiError(_STATUS[error.kind], error.message)


class NoteEndpoints:
    def _note_routes(self) -> list:
        ok = route
        return [
            ok(r"/notes", lambda ids, q, b: self.notes(q.get("q", ""))),
            ok(r"/notes", lambda ids, q, b: self.create_note(b), "POST", HTTPStatus.CREATED),
            ok(r"/notes/item", lambda ids, q, b: self.note(q.get("path", ""))),
            ok(r"/notes/item", lambda ids, q, b: self.update_note(b), "POST"),
            ok(r"/notes/item", lambda ids, q, b: self.delete_note(q.get("path", "")), "DELETE"),
            ok(r"/notes/rename", lambda ids, q, b: self.rename_note(b), "POST"),
        ]

    def _notes_root(self) -> Path:
        return notes.notes_dir(self.storage.db_path)

    def notes(self, query: str) -> list[dict]:
        return notes.list_notes(self._notes_root(), query)

    def note(self, path: str) -> dict:
        try:
            return notes.read_note(self._notes_root(), path)
        except notes.NoteError as error:
            raise _failed(error) from None

    def create_note(self, body: dict) -> dict:
        try:
            return notes.create_note(self._notes_root(), body.get("path"), body.get("content", ""))
        except notes.NoteError as error:
            raise _failed(error) from None

    def update_note(self, body: dict) -> dict:
        try:
            return notes.update_note(self._notes_root(), body.get("path"), body.get("content"), body.get("version"))
        except notes.NoteError as error:
            raise _failed(error) from None

    def rename_note(self, body: dict) -> dict:
        try:
            return notes.rename_note(self._notes_root(), body.get("path"), body.get("to"))
        except notes.NoteError as error:
            raise _failed(error) from None

    def delete_note(self, path: str) -> dict:
        try:
            notes.delete_note(self._notes_root(), path)
        except notes.NoteError as error:
            raise _failed(error) from None
        return {"ok": True}
