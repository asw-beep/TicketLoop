"""Paging for every list endpoint: `?limit=&cursor=` in, `{items, next_cursor}` out.

The cursor is opaque: clients pass back exactly what they were given. In M4 it holds an
offset (`{"o": 40}`, base64url-encoded). M6 switches it to the last row's sort key and id
(keyset paging) and compares the two; no client changes, because none ever looked inside.

Every query paged here must have a total order ending in `id`, so rows with equal sort
values still come back in a fixed order and no page repeats or skips one.
"""

import base64
import binascii
import json
from typing import Annotated, Any

from fastapi import Query
from sqlalchemy import Select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.core.schemas import ResponseModel

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
# Every bad cursor is a 400, so these are checked in decode_cursor, not by the query schema
# (which would answer 422). The offset cap keeps OFFSET far inside Postgres's bigint.
MAX_CURSOR_CHARS = 200
MAX_OFFSET = 2**31 - 1

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="Items per page, 1 to 100")]
Cursor = Annotated[str | None, Query(description="next_cursor from the previous page")]


class Page[T](ResponseModel):
    """One page of a list. next_cursor is null on the last page."""

    items: list[T]
    next_cursor: str | None


class BadCursorError(AppError):
    status = 400
    code = "bad-cursor"
    title = "The cursor is not valid"


def encode_cursor(offset: int) -> str:
    raw = json.dumps({"o": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(cursor: str | None) -> int:
    """The offset a cursor holds; 0 for no cursor. Anything we did not issue is a 400."""
    if cursor is None:
        return 0
    if len(cursor) > MAX_CURSOR_CHARS:
        raise BadCursorError("Pass back next_cursor exactly as you received it.")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data: Any = json.loads(raw)
    except (binascii.Error, ValueError):
        raise BadCursorError("Pass back next_cursor exactly as you received it.") from None
    offset = data.get("o") if isinstance(data, dict) else None
    # bool is a subclass of int in Python, so rule it out explicitly.
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise BadCursorError("Pass back next_cursor exactly as you received it.")
    return offset


def paginate[T](
    db: Session, stmt: Select[tuple[T]], cursor: str | None, limit: int
) -> tuple[list[T], str | None]:
    """Run `stmt` for one page. Fetches one row more than asked to learn if another page
    exists, so the last page never points at an empty one."""
    offset = decode_cursor(cursor)
    rows = list(db.scalars(stmt.offset(offset).limit(limit + 1)).unique().all())
    if len(rows) > limit:
        return rows[:limit], encode_cursor(offset + limit)
    return rows, None
