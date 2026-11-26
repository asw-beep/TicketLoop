"""The errors order code raises."""

from app.core.exceptions import ConflictError, NotFoundError


class OrderNotFoundError(NotFoundError):
    """One error for "no such order" and "not yours to see"."""

    code = "order-not-found"
    title = "Order not found"

    def __init__(self) -> None:
        super().__init__("Order not found.")


class ReservationNotPayableError(ConflictError):
    """The hold is over, or is past being ordered. Carries `reservation_status`, and
    `reason: hold-ended` when its ten minutes are up."""

    code = "reservation-not-payable"
    title = "This reservation can no longer be paid for"


class OrderExistsError(ConflictError):
    """This reservation already has an order. Carries its `order_id`: it is the caller's own."""

    code = "order-exists"
    title = "This reservation already has an order"


class IllegalOrderTransitionError(ConflictError):
    code = "illegal-order-transition"
    title = "The order cannot move to that status"


class OrderChangedError(ConflictError):
    """The order moved between our read and our write. Carries `current_status`."""

    code = "order-changed"
    title = "The order changed while you were acting on it"
