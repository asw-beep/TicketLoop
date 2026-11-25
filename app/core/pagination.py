"""Paging for every list endpoint: `?limit=&cursor=` in, `{items, next_cursor}` out.

Keyset paging. The cursor holds the last row's sort values, and the next page is "the rows
that sort after that row", a WHERE clause an index answers directly:

    WHERE (asking_price_paise, id) > (125000, '5f0c...') ORDER BY asking_price_paise, id LIMIT 21

An offset would make Postgres produce and throw away every earlier row, so page 500 costs 500
pages of work, and a row inserted before the reader's place would shift everything and show
one row twice. The cursor is opaque base64: clients pass it back unchanged, which is why M6
could replace the offset M4 and M5 put inside it without any client noticing.

Every sort ends in `id`, so rows with equal sort values still have one fixed order, and no
page repeats or skips one.
"""

import base64
import binascii
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Query
from sqlalchemy import BigInteger, ColumnElement, Select, literal, tuple_
from sqlalchemy.orm import InstrumentedAttribute, Session
from sqlalchemy.types import TypeEngine

from app.core.exceptions import AppError
from app.core.schemas import ResponseModel

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
# Every bad cursor is a 400, so the length is checked in decode_cursor, not by the query schema
# (which would answer 422).
MAX_CURSOR_CHARS = 200
# Postgres refuses a number its column type cannot hold, which would be a 500.
BIGINT = range(-(2**63), 2**63)
INTEGER = range(-(2**31), 2**31)

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


@dataclass(frozen=True)
class Sort:
    """One order a list can be read in. `name` goes into the cursor, so a cursor from one sort
    is refused by another. `keys` must end in an id. All keys run in the same direction: that
    is what lets one row comparison, and one index, serve the whole order."""

    name: str
    keys: tuple[ColumnElement[Any] | InstrumentedAttribute[Any], ...]
    descending: bool = False


def encode_cursor(sort: Sort, values: Sequence[Any]) -> str:
    keys = [_to_json(value) for value in values]
    raw = json.dumps({"s": sort.name, "k": keys}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(cursor: str, sort: Sort) -> tuple[Any, ...]:
    """The sort values a cursor holds. Anything we did not issue for this sort is a 400: the
    values go into a query, so each must be exactly the type its key compares with."""
    if len(cursor) > MAX_CURSOR_CHARS:
        raise _bad_cursor()
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data: Any = json.loads(raw)
    except (binascii.Error, ValueError):
        raise _bad_cursor() from None
    if not isinstance(data, dict) or data.get("s") != sort.name:
        raise _bad_cursor()
    values = data.get("k")
    if not isinstance(values, list) or len(values) != len(sort.keys):
        raise _bad_cursor()
    return tuple(_from_json(value, key.type) for value, key in zip(values, sort.keys, strict=True))


def page_stmt(stmt: Select[Any], sort: Sort, cursor: str | None, limit: int) -> Select[Any]:
    """The query for one page. The order is applied here, not by the caller, so it can never
    disagree with the cursor. Asks for one row more than the page holds, to learn whether
    another page exists. The key values come back with each row, after the entity, so a
    computed key needs no second definition."""
    if cursor is not None:
        values = decode_cursor(cursor, sort)
        bound = tuple_(*(literal(v, k.type) for v, k in zip(values, sort.keys, strict=True)))
        row = tuple_(*sort.keys)
        stmt = stmt.where(row < bound if sort.descending else row > bound)
    order = (key.desc() if sort.descending else key.asc() for key in sort.keys)
    return stmt.add_columns(*sort.keys).order_by(*order).limit(limit + 1)


def paginate[T](
    db: Session, stmt: Select[tuple[T]], sort: Sort, cursor: str | None, limit: int
) -> tuple[list[T], str | None]:
    """Run `stmt` for one page in `sort` order. The last page never points at an empty one."""
    rows = db.execute(page_stmt(stmt, sort, cursor, limit)).unique().all()
    items = [row[0] for row in rows[:limit]]
    if len(rows) > limit:
        return items, encode_cursor(sort, rows[limit - 1][1:])
    return items, None


def _bad_cursor() -> BadCursorError:
    return BadCursorError("Pass back next_cursor exactly as you received it.")


def _to_json(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _from_json(value: Any, column_type: TypeEngine[Any]) -> Any:
    kind = column_type.python_type
    if kind is int:
        allowed = BIGINT if isinstance(column_type, BigInteger) else INTEGER
        # bool is a subclass of int in Python, so compare types exactly.
        if type(value) is not int or value not in allowed:
            raise _bad_cursor()
        return value
    if not isinstance(value, str):
        raise _bad_cursor()
    try:
        if kind is uuid.UUID:
            return uuid.UUID(value)
        if kind is datetime:
            moment = datetime.fromisoformat(value)
            if moment.tzinfo is None:
                raise _bad_cursor()
            return moment
        value.encode()  # a lone surrogate cannot be sent to Postgres
    except ValueError:  # UnicodeEncodeError is one
        raise _bad_cursor() from None
    if "\x00" in value:  # nor can a NUL
        raise _bad_cursor()
    return value
