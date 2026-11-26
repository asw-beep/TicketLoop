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

# MinIO's root password in docker-compose.yml, and the key the API signs storage requests
# with on a laptop and in tests. Staging and production refuse to start with it.
LOCAL_STORAGE_SECRET = "ticketloop-local-secret"


# Signs fakepay's webhooks on a laptop and in tests. Staging and production refuse to start with
# it; anyone who reads this file could tell the API that an order has been paid for.
LOCAL_FAKEPAY_SECRET = "local-development-only-fakepay-secret-do-not-deploy"


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
    # Where reset emails link to: the frontend's reset page, which reads #token= from the URL.
    frontend_url: str = "http://localhost:5173"

    # Email. SMTP_HOST unset means: Mailpit on localhost on a laptop, and in tests an
    # in-memory list the tests read. See smtp_server below.
    smtp_host: str | None = None
    smtp_port: int = 1025
    email_from: str = "TicketLoop <no-reply@ticketloop.local>"

    # Object storage (M4): any S3-compatible server; MinIO in docker-compose.yml.
    # The API talks to storage_endpoint_url. Download links are signed for the host the
    # browser uses, which differs inside Docker (minio:9000 versus localhost:9000), so
    # storage_public_url overrides the host in presigned links. Unset means the same host.
    storage_endpoint_url: str = "http://localhost:9000"
    storage_public_url: str | None = None
    storage_bucket: str = "ticketloop-documents"
    storage_access_key: str = "ticketloop"
    storage_secret_key: SecretStr = SecretStr(LOCAL_STORAGE_SECRET)
    storage_region: str = "us-east-1"
    # The largest request body the API reads at all. Evidence files are up to 5 MiB, and the
    # multipart wrapping adds a little. Anything bigger is refused before it is read.
    max_request_bytes: int = 6 * 1024 * 1024

    # Payments (M8): the gateway, and the secret it signs its webhooks with.
    fakepay_url: str = "http://localhost:8090"
    fakepay_webhook_secret: SecretStr = SecretStr(LOCAL_FAKEPAY_SECRET)

    # Messaging (M9): RabbitMQ, where the outbox relay publishes and the workers consume.
    # queue_prefix keeps one set of queues apart from another on the same broker; the
    # messaging tests use one of their own so a development worker never takes their messages.
    broker_url: str = "amqp://ticketloop:ticketloop@localhost:5672//"
    queue_prefix: str = ""
    broker_connection_timeout_seconds: float = 5.0
    # The pretend ticket provider that moves a ticket to its buyer. Drills switch it to fail:
    # "retryable" fails `failures` times per transfer (0: always), "permanent" and "unknown"
    # fail every time; delay_seconds makes each call slow.
    ticket_provider_mode: Literal["succeed", "retryable", "permanent", "unknown"] = "succeed"
    ticket_provider_failures: int = 0
    ticket_provider_delay_seconds: float = 0.0

    # Redis (M10): a cache for the public reads and the rate limiter. Never part of a decision
    # about a ticket or money, and both fail open: Redis down means slower, not wrong.
    redis_url: str = "redis://localhost:6379/0"
    redis_timeout_seconds: float = 0.25
    cache_enabled: bool = True
    cache_ttl_min_seconds: int = 30
    cache_ttl_max_seconds: int = 60
    rate_limit_enabled: bool = True
    rate_limit_login_per_minute: int = 5  # per email and address; password-reset requests too
    rate_limit_reserve_per_minute: int = 10  # per user
    rate_limit_payment_per_minute: int = 10  # per user, payment intents
    rate_limit_search_per_minute: int = 60  # per address

    @property
    def storage_public_endpoint(self) -> str:
        """The storage host the browser reaches, used to sign download links."""
        return self.storage_public_url or self.storage_endpoint_url

    @property
    def cookie_secure(self) -> bool:
        """Mark cookies Secure (HTTPS only) everywhere except a laptop and tests, which use http."""
        return self.environment in ("staging", "production")

    @property
    def smtp_server(self) -> tuple[str, int] | None:
        """(host, port) to send email through, or None to keep it in memory."""
        host = self.smtp_host or ("localhost" if self.environment == "local" else None)
        return (host, self.smtp_port) if host else None

    @model_validator(mode="after")
    def _check_jwt_secret(self) -> Self:
        secret = self.jwt_secret_key.get_secret_value()
        if len(secret.encode()) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 bytes long")
        if self.environment in ("staging", "production") and secret == LOCAL_JWT_SECRET:
            raise ValueError(f"set JWT_SECRET_KEY: the built-in key is not for {self.environment}")
        return self

    @model_validator(mode="after")
    def _check_storage_secret(self) -> Self:
        deployed = self.environment in ("staging", "production")
        if deployed and self.storage_secret_key.get_secret_value() == LOCAL_STORAGE_SECRET:
            raise ValueError(
                f"set STORAGE_SECRET_KEY: the built-in key is not for {self.environment}"
            )
        return self

    @model_validator(mode="after")
    def _check_fakepay_secret(self) -> Self:
        secret = self.fakepay_webhook_secret.get_secret_value()
        if len(secret.encode()) < 32:
            raise ValueError("FAKEPAY_WEBHOOK_SECRET must be at least 32 bytes long")
        if self.environment in ("staging", "production") and secret == LOCAL_FAKEPAY_SECRET:
            raise ValueError(
                f"set FAKEPAY_WEBHOOK_SECRET: the built-in key is not for {self.environment}"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Build Settings once per process. Tests skip this and pass their own Settings."""
    return Settings()
