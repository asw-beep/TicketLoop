"""The opaque cursor: the sort it belongs to, and the last row's sort values."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.pagination import BadCursorError, Limit, Sort, decode_cursor, encode_cursor
from app.modules.events.models import Event, TicketProvider
from app.modules.listings.models import Listing

BY_PRICE = Sort("price", (Listing.asking_price_paise, Listing.id))
BY_PRICE_DOWN = Sort("-price", (Listing.asking_price_paise, Listing.id), descending=True)
BY_START = Sort("starts", (Event.starts_at, Event.id))
BY_CODE = Sort("code", (TicketProvider.code, TicketProvider.id))

AN_ID = uuid.UUID("5f0c1c8e-2f1b-4a57-9d8e-0a4f3f8f9c11")


def encode_raw(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def cursor_of(sort: str, *keys: Any) -> str:
    return encode_raw(json.dumps({"s": sort, "k": list(keys)}).encode())


@pytest.mark.parametrize(
    ("sort", "values"),
    [
        (BY_PRICE, (125_000, AN_ID)),
        (BY_START, (datetime(2027, 1, 15, 13, 0, 0, 123456, tzinfo=UTC), AN_ID)),
        (BY_CODE, ("fakeprovider", AN_ID)),
    ],
    ids=["int", "datetime to the microsecond", "str"],
)
def test_a_cursor_round_trips(sort: Sort, values: tuple[Any, ...]) -> None:
    assert decode_cursor(encode_cursor(sort, values), sort) == values


def test_a_cursor_keeps_the_instant_whatever_its_zone() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    noon = datetime(2027, 1, 15, 12, 0, tzinfo=ist)

    decoded = decode_cursor(encode_cursor(BY_START, (noon, AN_ID)), BY_START)

    assert decoded[0] == noon


def test_a_cursor_does_not_show_its_values_in_the_clear() -> None:
    assert "125000" not in encode_cursor(BY_PRICE, (125_000, AN_ID))


def test_a_cursor_fits_the_length_limit() -> None:
    assert len(encode_cursor(BY_START, (datetime.now(UTC), AN_ID))) <= 200


def test_a_cursor_from_another_sort_is_refused() -> None:
    cursor = encode_cursor(BY_PRICE, (125_000, AN_ID))

    with pytest.raises(BadCursorError):
        decode_cursor(cursor, BY_PRICE_DOWN)


@pytest.mark.parametrize(
    "bad",
    [
        "not base64!",
        "é",
        encode_raw(b"not json"),
        encode_raw(b"\xff\xfe"),
        encode_raw(b"[]"),
        encode_raw(b"{}"),
        encode_raw(b'{"o": 40}'),  # the offset cursor M4 and M5 issued
        encode_raw(b'{"s": "price"}'),
        encode_raw(b'{"s": "price", "k": 5}'),
        cursor_of("price"),
        cursor_of("price", 1),
        cursor_of("price", 1, str(AN_ID), 2),
        cursor_of("price", "1", str(AN_ID)),
        cursor_of("price", 1.5, str(AN_ID)),
        cursor_of("price", True, str(AN_ID)),
        cursor_of("price", None, str(AN_ID)),
        cursor_of("price", 2**63, str(AN_ID)),  # beyond Postgres's bigint
        cursor_of("price", -(2**63) - 1, str(AN_ID)),
        cursor_of("price", 1, "not-a-uuid"),
        cursor_of("price", 1, 7),
        encode_raw(b'{"s":"price","k":[1' + b"0" * 60 + b',"x"]}'),
        "A" * 201,
        "",
    ],
)
def test_a_tampered_cursor_is_refused(bad: str) -> None:
    with pytest.raises(BadCursorError):
        decode_cursor(bad, BY_PRICE)


@pytest.mark.parametrize(
    "value",
    ["2027-01-15T13:00:00", "yesterday", 5, None, ""],
    ids=["no time zone", "not a time", "a number", "null", "empty"],
)
def test_a_tampered_time_is_refused(value: Any) -> None:
    with pytest.raises(BadCursorError):
        decode_cursor(cursor_of("starts", value, str(AN_ID)), BY_START)


@pytest.mark.parametrize("value", ["a\x00b", "\ud800", 5, None], ids=["nul", "surrogate", "n", "0"])
def test_a_tampered_text_is_refused(value: Any) -> None:
    with pytest.raises(BadCursorError):
        decode_cursor(cursor_of("code", value, str(AN_ID)), BY_CODE)


@pytest.mark.parametrize("limit", [0, 101])
def test_limits_outside_1_to_100_are_422(limit: int) -> None:
    probe = FastAPI()

    @probe.get("/probe")
    def route(limit: Limit = 20) -> int:
        return limit

    assert TestClient(probe).get("/probe", params={"limit": limit}).status_code == 422


def test_the_default_limit_is_20() -> None:
    probe = FastAPI()

    @probe.get("/probe")
    def route(limit: Limit = 20) -> int:
        return limit

    assert TestClient(probe).get("/probe").json() == 20
