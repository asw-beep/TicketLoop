"""Application settings, read from environment variables and an optional local .env file.

Precedence, highest first: real environment variables, then .env, then the defaults below.
The defaults match docker-compose.yml so a fresh clone runs without any setup, but real
deployments must set every secret through the environment.
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Signs access tokens on a laptop and in tests only. Staging and production refuse to start
# with it; anyone who reads this file could forge a token for any user.
LOCAL_JWT_SECRET = "local-development-only-jwt-secret-do-not-deploy"


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

    # Auth (M3). SecretStr prints as '**********', so the key never lands in a log or a repr.
    # HS256 wants a key at least as long as its 32-byte output. Generate one with:
    #   python -c "import secrets; print(secrets.token_urlsafe(48))"
    jwt_secret_key: SecretStr = SecretStr(LOCAL_JWT_SECRET)
    jwt_issuer: str = "ticketloop"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    password_reset_minutes: int = 30

    @model_validator(mode="after")
    def _check_jwt_secret(self) -> Self:
        secret = self.jwt_secret_key.get_secret_value()
        if len(secret.encode()) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 bytes long")
        if self.environment in ("staging", "production") and secret == LOCAL_JWT_SECRET:
            raise ValueError(f"set JWT_SECRET_KEY: the built-in key is not for {self.environment}")
        return self


@lru_cache
def get_settings() -> Settings:
    """Build Settings once per process. Tests skip this and pass their own Settings."""
    return Settings()
