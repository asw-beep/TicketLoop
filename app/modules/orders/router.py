"""HTTP routes for orders. Thin: validate, call the service, shape."""

import uuid

from fastapi import APIRouter, Request, Response

from app.core.pagination import Cursor, Limit, Page
from app.infrastructure.database.session import DbSession
from app.modules.idempotency import service as idempotency
from app.modules.idempotency.dependencies import IdempotencyKey
from app.modules.identity.dependencies import CurrentUser
from app.modules.orders import service
from app.modules.orders.models import OrderStatus
from app.modules.orders.schemas import OrderIn, OrderOut, OrderRole

orders_router = APIRouter(prefix="/orders", tags=["orders"])


@orders_router.post("", response_model=OrderOut, status_code=201)
def create_order(
    body: OrderIn,
    key: IdempotencyKey,
    user: CurrentUser,
    db: DbSession,
    request: Request,
    response: Response,
) -> OrderOut:
    """Order the ticket you are holding, at the listing's price. Sending the same request again
    with the same Idempotency-Key returns the same response and makes nothing new."""
    asked = idempotency.Asked.of(user, key, request, body)
    stored = idempotency.claim(db, asked)
    if stored is not None:
        return OrderOut.model_validate(idempotency.replay(response, stored))
    order = service.create_order(db, body.reservation_id, user, key)
    out = OrderOut.model_validate(order)
    idempotency.store(db, asked, 201, out)
    return out


# Declared before /{order_id}, or "me" would be read as a (malformed) ID.
@orders_router.get("/me", response_model=Page[OrderOut])
def my_orders(
    user: CurrentUser,
    db: DbSession,
    role: OrderRole = "buyer",
    status: OrderStatus | None = None,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Page[OrderOut]:
    """What you bought, or with `role=seller` what you sold, newest first."""
    items, next_cursor = service.list_my_orders(db, user, role, status, cursor, limit)
    return Page[OrderOut](items=items, next_cursor=next_cursor)


@orders_router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OrderOut:
    """One order, for its buyer, its seller and admins."""
    return OrderOut.model_validate(service.get_order_for(db, order_id, user))
