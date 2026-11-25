"""The errors listing code raises. Each becomes a problem+json response with its own `type`."""

from app.core.exceptions import AppError, ConflictError, NotFoundError, PermissionDeniedError


class ListingNotFoundError(NotFoundError):
    """One error, with one fixed detail, for "no such listing" and "you may not see it", so a
    response never tells anyone whether a listing they cannot see exists."""

    code = "listing-not-found"
    title = "Listing not found"

    def __init__(self) -> None:
        super().__init__("Listing not found.")


class IllegalListingTransitionError(ConflictError):
    """The state machine has no such move. Carries `current_status`."""

    code = "illegal-listing-transition"
    title = "The listing cannot move to that status"


class ListingChangedError(ConflictError):
    """Someone changed the listing between our read and our write. Carries `current_status`."""

    code = "listing-changed"
    title = "The listing changed while you were acting on it"


class PriceOutOfRangeError(AppError):
    status = 422
    code = "price-out-of-range"
    title = "The asking price is out of range"


class PriceIncreaseError(ConflictError):
    code = "price-increase-not-allowed"
    title = "An active listing's price can only go down"


class BadExpiryError(AppError):
    status = 422
    code = "bad-expiry"
    title = "The expiry must be in the future and no later than the event start"


class TicketNotListableError(ConflictError):
    code = "ticket-not-listable"
    title = "This ticket cannot be listed"


class ListingAlreadyOpenError(ConflictError):
    code = "listing-already-open"
    title = "This ticket already has an open listing"


class ListingNotEditableError(ConflictError):
    code = "listing-not-editable"
    title = "This listing can no longer be edited"


class ListingNotDraftError(ConflictError):
    code = "listing-not-draft"
    title = "Only a draft listing can be submitted"


class ListingNotCancellableError(ConflictError):
    code = "listing-not-cancellable"
    title = "This listing cannot be cancelled"


class ListingNotPendingError(ConflictError):
    code = "listing-not-pending"
    title = "Only a listing waiting for review can be reviewed"


class ListingNotApprovableError(ConflictError):
    code = "listing-not-approvable"
    title = "This listing cannot be approved"


class SellerOnlyError(PermissionDeniedError):
    """An admin can see the listing, so a 404 would hide nothing; they review listings, they
    do not edit or submit them."""

    code = "seller-only"
    title = "Only the listing's seller can do this"


class OwnListingReviewError(PermissionDeniedError):
    """Self-review is exactly what an audit log exists to catch, so it is refused outright."""

    code = "own-listing-review"
    title = "You cannot review your own listing"


class EventHasReservationsError(ConflictError):
    """An event cannot be cancelled while a buyer is holding one of its tickets: a hold may be
    a payment in progress. Carries `reserved_listings`, how many."""

    code = "event-has-reservations"
    title = "A buyer is holding one of this event's tickets"
