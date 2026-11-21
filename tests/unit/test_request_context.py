import io
import json
import logging
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.health import database_is_reachable
from app.core.logging import build_formatter


def test_every_response_gets_a_generated_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


def test_a_safe_incoming_request_id_is_kept(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist", headers={"X-Request-ID": "trace-abc.123"})

    assert response.headers["X-Request-ID"] == "trace-abc.123"


def test_an_unsafe_incoming_request_id_is_replaced(client: TestClient) -> None:
    sneaky = '"}{"level":"critical' + "x" * 80

    response = client.get("/api/v1/does-not-exist", headers={"X-Request-ID": sneaky})

    assert response.headers["X-Request-ID"] != sneaky
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


def test_log_lines_are_json_and_carry_the_request_id(app: FastAPI, client: TestClient) -> None:
    app.dependency_overrides[database_is_reachable] = lambda: True
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(build_formatter(json_logs=True))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        response = client.get("/api/v1/health")
    finally:
        root.removeHandler(handler)

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]  # fails if not JSON
    # The test client logs its own outgoing request; that happens outside the server's
    # request context, so leave it out. Everything the server logged must carry the ID.
    server_lines = [line for line in lines if not line["logger"].startswith("httpx")]
    access = [line for line in server_lines if line["event"] == "http.request"]
    assert len(access) == 1
    assert access[0]["status"] == 200
    assert access[0]["path"] == "/api/v1/health"
    assert all(line["request_id"] == response.headers["X-Request-ID"] for line in server_lines)
