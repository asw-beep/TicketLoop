"""The errors payment code raises."""

from app.core.exceptions import AppError, ConflictError, NotFoundError


class PaymentNotFoundError(NotFoundError):
    """One error for "no such payment" and "not yours to see"."""

    code = "payment-not-found"
    title = "Payment not found"

    def __init__(self) -> None:
        super().__init__("Payment not found.")


class UnknownProviderError(NotFoundError):
    code = "unknown-payment-provider"
    title = "No such payment provider"


class OrderNotPayableError(ConflictError):
    """Carries `current_status`."""

    code = "order-not-payable"
    title = "This order can no longer be paid for"


class PaymentGatewayUnavailableError(AppError):
    """The gateway gave no usable answer. The payment is kept, and trying again continues it."""

    status = 503
    code = "payment-gateway-unavailable"
    title = "The payment service is not answering"
    headers = {"Retry-After": "5"}


class PaymentRefusedError(AppError):
    status = 502
    code = "payment-refused"
    title = "The payment service refused this payment"


class BadWebhookSignatureError(AppError):
    """One answer for a missing, wrong or stale signature: no hints for a forger."""

    status = 401
    code = "bad-webhook-signature"
    title = "The webhook's signature or timestamp is not valid"


class BadWebhookBodyError(AppError):
    status = 400
    code = "bad-webhook-body"
    title = "The webhook's body is not readable"


class IllegalPaymentTransitionError(ConflictError):
    code = "illegal-payment-transition"
    title = "The payment cannot move to that status"


class PaymentChangedError(ConflictError):
    """The payment moved between our read and our write. Carries `current_status`."""

    code = "payment-changed"
    title = "The payment changed while you were acting on it"
