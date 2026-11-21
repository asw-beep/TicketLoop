"""Application settings, read from environment variables and an optional local .env file.

Precedence, highest first: real environment variables, then .env, then the defaults below.
The defaults match docker-compose.yml so a fresh clone runs without any setup, but real
deployments must set every secret through the environment.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # .env also holds POSTGRES_* variables meant for docker compose; ignore what we don't use.
        extra="ignore",
    )

    app_name: str = "TicketLoop"
    environment: Literal["local", "test", "staging", "production"] = "local"
    api_prefix: str = "/api/v1"

    # postgresql+psycopg tells SQLAlchemy to use the psycopg 3 driver.
    database_url: str = "postgresql+psycopg://ticketloop:ticketloop@localhost:5432/ticketloop"
    # How long to wait for Postgres to accept a connection. libpq treats values below 2 as 2.
    db_connect_timeout_seconds: int = 3

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # True: one JSON object per line (containers, log tools). False: coloured console output.
    log_json: bool = True


@lru_cache
def get_settings() -> Settings:
    """Build Settings once per process. Tests skip this and pass their own Settings."""
    return Settings()
