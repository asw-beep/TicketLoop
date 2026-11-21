"""Shared pytest fixtures. pytest finds this file by name and makes its fixtures available to
every test below this folder; a test asks for one by naming it as a parameter."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: ignore any developer .env so tests behave the same on every machine.
    return Settings(_env_file=None, environment="test")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # TestClient calls the app in-process; no server or network involved.
    # The `with` block also runs the app's startup and shutdown (lifespan).
    with TestClient(app) as test_client:
        yield test_client
