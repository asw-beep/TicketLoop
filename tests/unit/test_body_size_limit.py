"""Request bodies over the limit are refused before the app reads them."""

from fastapi.testclient import TestClient

from app.core.config import Settings


def test_a_body_over_the_limit_is_413_before_it_is_read(
    client: TestClient, settings: Settings
) -> None:
    too_big = b"\0" * (settings.max_request_bytes + 1)

    response = client.post(
        "/api/v1/tickets/00000000-0000-4000-8000-000000000000/documents",
        content=too_big,
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"].endswith("file-too-large")
    assert body["request_id"] == response.headers["x-request-id"]


def test_a_body_at_the_limit_reaches_the_app(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/api/v1/health",
        content=b"\0" * settings.max_request_bytes,
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code != 413  # 405: the app saw it and said GET only


def test_a_garbled_content_length_is_400(client: TestClient) -> None:
    response = client.post("/api/v1/health", headers={"Content-Length": "lots"})

    assert response.status_code == 400


def chunked(data: bytes, size: int = 64 * 1024):
    """A body sent in pieces with no Content-Length, as Transfer-Encoding: chunked does."""
    for start in range(0, len(data), size):
        yield data[start : start + size]


def test_a_chunked_body_over_the_limit_is_cut_off_with_413(
    client: TestClient, settings: Settings
) -> None:
    # No Content-Length to look at, so the bytes are counted as they arrive. Anonymous on
    # purpose: FastAPI parses the form before it checks the token, so without the count a
    # stranger could stream gigabytes to the temp directory and only then hear 401.
    boundary = "ticketloopboundary"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
        + b"\0" * (settings.max_request_bytes + 1)
        + f"\r\n--{boundary}--\r\n".encode()
    )

    response = client.post(
        "/api/v1/tickets/00000000-0000-4000-8000-000000000000/documents",
        content=chunked(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"].endswith("file-too-large")


def test_a_small_chunked_body_reaches_the_app(client: TestClient) -> None:
    response = client.post(
        "/api/v1/health",
        content=chunked(b"\0" * 1000),
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 405  # the app saw it and said GET only
