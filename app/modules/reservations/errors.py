"""The errors reservation code raises."""

from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError


class ReservationNotFoundError(NotFoundError):
    """One error for "no such reservation" and "not yours to see"."""

    code = "reservation-not-found"
    title = "Reservation not found"

    def __init__(self) -> None:
        super().__init__("Reservation not found.")


class OwnListingReservationError(PermissionDeniedError):
    code = "own-listing-reservation"
    title = "You cannot reserve your own listing"


class BuyerOnlyError(PermissionDeniedError):
    code = "buyer-only"
    title = "Only the buyer can do this"


class ListingNotAvailableError(ConflictError):
    """Nobody can reserve this listing right now. Carries `reason`: reserved, sold, expired, or
    hold-ended when the caller's own hold ran out a moment ago."""

    code = "listing-not-available"
    title = "The listing is not available"


class IllegalReservationTransitionError(ConflictError):
    code = "illegal-reservation-transition"
    title = "The reservation cannot move to that status"


class ReservationChangedError(ConflictError):
    """The reservation moved between our read and our write. Carries `current_status`."""

    code = "reservation-changed"
    title = "The reservation changed while you were acting on it"


class ReservationNotCancellableError(ConflictError):
    code = "reservation-not-cancellable"
    title = "This reservation can no longer be cancelled"
