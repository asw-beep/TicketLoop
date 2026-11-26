"""Shared pytest fixtures. pytest finds this file by name and makes its fixtures available to
every test below this folder; a test asks for one by naming it as a parameter."""

from collections.abc import Iterator

import pytest
from argon2 import PasswordHasher, profiles
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import security
from app.core.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def cheap_password_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real Argon2 settings cost about 50 ms and 64 MiB per hash, on purpose. Tests hash
    # hundreds of passwords, so they use the cheapest valid parameters instead. One unit test
    # (test_security.py) switches the real hasher back on to check its settings.
    monkeypatch.setattr(security, "_hasher", PasswordHasher.from_parameters(profiles.CHEAPEST))


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: ignore any developer .env so tests behave the same on every machine.
    return Settings(
        _env_file=None, environment="test", cache_enabled=False, rate_limit_enabled=False
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # TestClient calls the app in-process; no server or network involved.
    # The `with` block also runs the app's startup and shutdown (lifespan).
    with TestClient(app) as test_client:
        yield test_client
