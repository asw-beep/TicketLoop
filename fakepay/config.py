"""fakepay's settings, from FAKEPAY_* environment variables."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# The same value as app/core/config.py's built-in one, for a laptop and tests.
LOCAL_WEBHOOK_SECRET = "local-development-only-fakepay-secret-do-not-deploy"


class FakepaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FAKEPAY_", extra="ignore")

    webhook_secret: SecretStr = SecretStr(LOCAL_WEBHOOK_SECRET)
    # Where this gateway sends webhooks: the TicketLoop API.
    webhook_url: str = "http://localhost:8000/api/v1/payments/webhooks/fakepay"
    # Where a browser reaches this gateway: checkout pages live here.
    public_url: str = "http://localhost:8090"
    # An SQLite file, so a restart forgets nothing. ":memory:" for tests.
    db: str = ":memory:"
    intent_minutes: int = 15
    delay_seconds: float = 10.0
    # How long to wait before each further try when the API does not answer 2xx.
    retry_waits: tuple[float, ...] = (1.0, 3.0, 9.0)
