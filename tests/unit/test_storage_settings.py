"""Storage settings and the in-memory storage the API tests use instead of MinIO."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.infrastructure.storage import InMemoryStorage


def test_production_refuses_the_built_in_storage_secret() -> None:
    with pytest.raises(ValidationError, match="STORAGE_SECRET_KEY"):
        Settings(_env_file=None, environment="production", jwt_secret_key="x" * 40)


def test_production_starts_with_its_own_storage_secret() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        jwt_secret_key="x" * 40,
        storage_secret_key="a-real-secret-from-the-environment",
    )
    assert settings.storage_secret_key.get_secret_value() == "a-real-secret-from-the-environment"


def test_public_endpoint_defaults_to_the_endpoint() -> None:
    settings = Settings(_env_file=None, storage_endpoint_url="http://minio:9000")
    assert settings.storage_public_endpoint == "http://minio:9000"

    public = settings.model_copy(update={"storage_public_url": "http://localhost:9000"})
    assert public.storage_public_endpoint == "http://localhost:9000"


def test_in_memory_storage_keeps_bytes_and_type() -> None:
    storage = InMemoryStorage()
    storage.put_object("k", b"%PDF-1", "application/pdf")

    assert storage.objects["k"] == (b"%PDF-1", "application/pdf")
    assert "k" in storage.presigned_get_url("k", "application/pdf")
