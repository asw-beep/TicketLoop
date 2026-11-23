"""Price and expiry rules for listings. Pure functions: no database, no clock of their own.

The database CHECKs (ck_listings_min_price, ck_listings_price_cap) stay as the last line of
defence. These checks run first so a caller gets a clear 4xx instead of a CHECK violation.
"""

from datetime import datetime

from app.modules.listings.errors import BadExpiryError, PriceIncreaseError, PriceOutOfRangeError
from app.modules.listings.models import ListingStatus

MIN_PRICE_PAISE = 100  # ₹1


def check_price(
    asking: int,
    original: int,
    *,
    current: int | None = None,
    status: ListingStatus | None = None,
) -> None:
    """From ₹1 to the ticket's face value, inclusive. An ACTIVE listing may only get cheaper:
    buyers have already seen its price."""
    if not MIN_PRICE_PAISE <= asking <= original:
        raise PriceOutOfRangeError(
            f"The asking price must be from {MIN_PRICE_PAISE} to {original} paise."
        )
    if status == ListingStatus.ACTIVE and current is not None and asking > current:
        raise PriceIncreaseError(
            f"An active listing's price can go down from {current} paise, not up."
        )


def check_expiry(expires_at: datetime, event_starts_at: datetime, now: datetime) -> None:
    """Strictly after `now`, and no later than the event start: a listing never outlives the
    event it sells a seat for."""
    if expires_at <= now:
        raise BadExpiryError("The expiry must be in the future.")
    if expires_at > event_starts_at:
        raise BadExpiryError("The expiry cannot be later than the event start.")
