"""The S3 adapter against a real MinIO: links open, expire, and the bucket stays private."""

import time
import uuid

import httpx2 as httpx
import pytest

from app.core.config import Settings
from app.infrastructure.storage import S3Storage, StorageError
from tests.integration.files import PNG_BYTES

pytestmark = pytest.mark.integration


def new_key() -> str:
    return f"ticket-documents/{uuid.uuid4().hex}"


def test_a_presigned_link_serves_the_object_with_its_type(s3_storage: S3Storage) -> None:
    key = new_key()
    s3_storage.put_object(key, PNG_BYTES, "image/png")

    response = httpx.get(s3_storage.presigned_get_url(key, "image/png"))

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG_BYTES


def test_the_bucket_refuses_unsigned_reads(s3_storage: S3Storage) -> None:
    key = new_key()
    s3_storage.put_object(key, PNG_BYTES, "image/png")

    unsigned = s3_storage.presigned_get_url(key, "image/png").split("?")[0]

    assert httpx.get(unsigned).status_code == 403


def test_a_link_stops_working_after_it_expires(s3_storage: S3Storage) -> None:
    key = new_key()
    s3_storage.put_object(key, PNG_BYTES, "image/png")
    url = s3_storage.presigned_get_url(key, "image/png", expires_in=1)

    time.sleep(2)

    assert httpx.get(url).status_code == 403


def test_put_object_raises_storage_error_when_the_server_is_down(
    test_settings: Settings,
) -> None:
    down = test_settings.model_copy(update={"storage_endpoint_url": "http://localhost:9"})

    with pytest.raises(StorageError):
        S3Storage.from_settings(down).put_object("k", b"x", "image/png")
