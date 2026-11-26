"""HTTP routes for reservations. Thin: validate, call the service, shape."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.rate_limits import per_user
from app.core.exceptions import AppError
from app.core.metrics import RESERVATION_ATTEMPTS, reservation_result
from app.core.pagination import Cursor, Limit, Page
from app.infrastructure.database.session import DbSession
from app.modules.identity.dependencies import CurrentUser
from app.modules.reservations import service
from app.modules.reservations.schemas import ReservationOut

reservations_router = APIRouter(tags=["reservations"])


@reservations_router.post(
    "/listings/{listing_id}/reservations",
    response_model=ReservationOut,
    status_code=201,
    responses={200: {"model": ReservationOut, "description": "You already hold it"}},
)
def reserve(
    listing_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    response: Response,
    _: Annotated[None, Depends(per_user("reserve"))],
) -> ReservationOut:
    """Hold an ACTIVE listing for 10 minutes while you pay. Of many buyers at once exactly one
    gets it. Asking again while you hold it returns the hold you have."""
    try:
        reservation, created = service.reserve(db, listing_id, user)
    except AppError as error:
        RESERVATION_ATTEMPTS.labels(reservation_result(error.code)).inc()
        raise
    RESERVATION_ATTEMPTS.labels("reserved" if created else "already_yours").inc()
    if not created:
        response.status_code = 200
    return ReservationOut.model_validate(reservation)


# Declared before /{reservation_id}, or "me" would be read as a (malformed) ID.
@reservations_router.get("/reservations/me", response_model=Page[ReservationOut])
def my_reservations(
    user: CurrentUser, db: DbSession, cursor: Cursor = None, limit: Limit = 20
) -> Page[ReservationOut]:
    """Your reservations: the ones still waiting for you first, then newest first."""
    items, next_cursor = service.list_my_reservations(db, user, cursor, limit)
    return Page[ReservationOut](items=items, next_cursor=next_cursor)


@reservations_router.get("/reservations/{reservation_id}", response_model=ReservationOut)
def get_reservation(reservation_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ReservationOut:
    """One reservation, for its buyer, the listing's seller and admins."""
    return ReservationOut.model_validate(service.get_reservation_for(db, reservation_id, user))


@reservations_router.post("/reservations/{reservation_id}/cancel", response_model=ReservationOut)
def cancel_reservation(
    reservation_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> ReservationOut:
    """Give up your hold before paying. The ticket goes back on sale."""
    return ReservationOut.model_validate(service.cancel_reservation(db, reservation_id, user))
