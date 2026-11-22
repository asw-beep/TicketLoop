"""The opaque cursor: an offset in M4, wrapped so M6 can change what it holds."""

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.pagination import BadCursorError, Limit, decode_cursor, encode_cursor


def encode_raw(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_a_cursor_round_trips() -> None:
    assert decode_cursor(encode_cursor(40)) == 40


def test_no_cursor_means_the_first_page() -> None:
    assert decode_cursor(None) == 0


def test_a_cursor_does_not_show_a_bare_number() -> None:
    assert encode_cursor(40) != "40"


@pytest.mark.parametrize(
    "bad",
    [
        "not base64!",
        encode_raw(b"not json"),
        encode_raw(b"[]"),
        encode_raw(b"{}"),
        encode_raw(b'{"o": -1}'),
        encode_raw(b'{"o": "x"}'),
        encode_raw(b'{"o": true}'),
        encode_raw(b'{"o": 1.5}'),
        # Far beyond any real list, and beyond Postgres's bigint: OFFSET would raise a 500.
        encode_raw(b'{"o": 1000000000000000000000000000000}'),
        "A" * 201,
    ],
)
def test_a_tampered_cursor_is_refused(bad: str) -> None:
    with pytest.raises(BadCursorError):
        decode_cursor(bad)


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
