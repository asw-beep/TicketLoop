"""Request and response bodies for /payments. Webhook bodies are in webhooks.py."""

import uuid

from pydantic import AwareDatetime, BaseModel

from app.core.schemas import RequestModel, ResponseModel
from app.modules.payments.models import PaymentStatus


class IntentIn(RequestModel):
    order_id: uuid.UUID


class IntentOut(BaseModel):
    """Where to send the buyer to pay."""

    payment_id: uuid.UUID
    provider: str
    provider_transaction_id: str
    checkout_url: str
    amount_paise: int
    currency: str
    status: PaymentStatus


class PaymentOut(ResponseModel):
    """`Payment` in the API contract. Nothing about cards: none ever reach us."""

    id: uuid.UUID
    order_id: uuid.UUID
    provider: str
    status: PaymentStatus
    amount_paise: int
    currency: str
    failure_reason: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class WebhookAck(BaseModel):
    received: bool = True
