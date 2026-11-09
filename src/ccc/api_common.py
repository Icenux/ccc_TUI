"""What the parts of ccc's HTTP API (ccc.api, ccc.api_agents,
ccc.api_views) share: the error they raise, reading a request, writing
times, and a route of the route table."""
from __future__ import annotations

import re
from datetime import datetime
from http import HTTPStatus

from ccc.i18n import _


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def route(pattern: str, handler, method: str = "GET", status: HTTPStatus = HTTPStatus.OK):
    """A route: (method, path after /api/v1, status, handler(ids, query, body)).
    A number in the path is passed in `ids` as an int, anything else as text."""
    return method, re.compile(pattern), status, handler


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def text_of(body: dict, key: str) -> str:
    value = body.get(key)
    return value if isinstance(value, str) else ""


def int_of(query: dict[str, str], key: str) -> int | None:
    raw = query.get(key)
    if raw is None:
        return None
    if not re.fullmatch(r"-?\d+", raw):
        raise ApiError(HTTPStatus.BAD_REQUEST, _("{name} must be a number.").format(name=key))
    return int(raw)
