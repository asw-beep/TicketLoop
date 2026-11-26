"""HTTP routes for payments. Thin: validate, call the service, shape."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.rate_limits import per_user
from app.core.dependencies import AppSettings
from app.core.exceptions import AppError
from app.core.metrics import PAYMENT_EVENTS, WEBHOOK_SECONDS, payment_type
from app.infrastructure.database.session import DbSession
from app.infrastructure.payments import GatewayRefused, GatewayUnavailable, PaymentGateway
from app.modules.idempotency import service as idempotency
from app.modules.idempotency.dependencies import IdempotencyKey
from app.modules.identity.dependencies import CurrentUser
from app.modules.payments import reconcile, service, webhooks
from app.modules.payments.errors import (
    PaymentGatewayUnavailableError,
    PaymentRefusedError,
    UnknownProviderError,
)
from app.modules.payments.models import PROVIDER
from app.modules.payments.schemas import IntentIn, IntentOut, PaymentOut, WebhookAck

payments_router = APIRouter(prefix="/payments", tags=["payments"])

STILL_HELD = "Your ticket is still held. Try again in a moment."


def get_gateway(request: Request) -> PaymentGateway:
    return request.app.state.payment_gateway


Gateway = Annotated[PaymentGateway, Depends(get_gateway)]


@payments_router.post(
    "/intents",
    response_model=IntentOut,
    status_code=201,
    responses={200: {"model": IntentOut, "description": "Your payment is already open"}},
)
def create_intent(
    body: IntentIn,
    key: IdempotencyKey,
    user: CurrentUser,
    db: DbSession,
    gateway: Gateway,
    settings: AppSettings,
    request: Request,
    response: Response,
    _: Annotated[None, Depends(per_user("payment"))],
) -> IntentOut:
    """Start paying for an order, or continue: send the buyer to `checkout_url`."""
    asked = idempotency.Asked.of(user, key, request, body)
    stored = idempotency.claim(db, asked, lock=True)
    if stored is not None:
        return IntentOut.model_validate(idempotency.replay(response, stored))
    payment, new = service.start_payment(db, body.order_id, user)

    # The one commit outside get_db. From here money may move, so the payment must exist
    # whatever happens to this process, and no transaction (or pooled connection) may be held
    # while we wait for someone else's server.
    db.commit()

    try:
        try:
            intent = gateway.create_intent(
                idempotency_key=payment.idempotency_key,
                amount_paise=payment.amount_paise,
                currency=payment.currency,
                return_url=f"{settings.frontend_url}/orders/{payment.order_id}",
            )
        except GatewayUnavailable as error:
            raise PaymentGatewayUnavailableError(STILL_HELD) from error
        except GatewayRefused as error:
            if service.refused(db, payment.id, new=new):
                raise PaymentRefusedError("The ticket has gone back on sale.") from error
            raise PaymentGatewayUnavailableError(STILL_HELD) from error
        try:
            payment = service.take_intent(db, payment.id, intent)
        except service.IntentMismatch as error:
            raise PaymentGatewayUnavailableError(STILL_HELD) from error
        if intent.status != "open":
            # Paid already, or past paying: settle it now, as reconciliation would. Handing out
            # its page again would only say so, and would keep the ticket from everyone else
            # for as long as the buyer kept coming back.
            reconcile.apply(db, payment, intent, source="checkout")
        service.check_open(payment)
    except AppError:
        # Keep what was just recorded, and let the same key try again.
        idempotency.release(db, asked)
        db.commit()
        raise

    out = IntentOut(
        payment_id=payment.id,
        provider=payment.provider,
        provider_transaction_id=intent.transaction_id,
        checkout_url=intent.checkout_url,
        amount_paise=payment.amount_paise,
        currency=payment.currency,
        status=payment.status,
    )
    response.status_code = 201 if new else 200
    idempotency.store(db, asked, response.status_code, out)
    return out


async def raw_body(request: Request) -> bytes:
    """The exact bytes: the signature is over these, not over what they parse to."""
    return await request.body()


@payments_router.post("/webhooks/{provider}", response_model=WebhookAck)
def receive_webhook(
    provider: str,
    request: Request,
    body: Annotated[bytes, Depends(raw_body)],
    settings: AppSettings,
    db: DbSession,
) -> WebhookAck:
    """For the gateway only, which signs what it sends. 200 means "we have it", for a repeat
    too; anything else and it sends the event again."""
    with WEBHOOK_SECONDS.time():
        try:
            if provider != PROVIDER:
                raise UnknownProviderError()
            webhooks.verify(
                settings.fakepay_webhook_secret.get_secret_value(),
                body,
                request.headers.get(webhooks.SIGNATURE_HEADER),
                request.headers.get(webhooks.TIMESTAMP_HEADER),
                webhooks.now(),
            )
            event = webhooks.parse(body)
        except AppError:
            PAYMENT_EVENTS.labels("unknown", "rejected").inc()
            raise
        outcome = webhooks.receive(db, event)
        PAYMENT_EVENTS.labels(
            payment_type(event.type), outcome.value.lower() if outcome else "duplicate"
        ).inc()
    return WebhookAck()


@payments_router.get("/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: uuid.UUID, user: CurrentUser, db: DbSession) -> PaymentOut:
    """One payment, for its order's buyer and admins."""
    return PaymentOut.model_validate(service.get_payment_for(db, payment_id, user))
