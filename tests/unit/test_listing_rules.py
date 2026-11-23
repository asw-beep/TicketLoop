"""Price and expiry rules for listings: pure functions, checked at their boundaries."""

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.listings.errors import BadExpiryError, PriceIncreaseError, PriceOutOfRangeError
from app.modules.listings.models import ListingStatus
from app.modules.listings.rules import check_expiry, check_price


@pytest.mark.parametrize("asking", [100, 250_000])
def test_prices_from_one_rupee_to_face_value_pass(asking):
    check_price(asking, 250_000)


@pytest.mark.parametrize("asking", [99, 250_001])
def test_prices_outside_the_range_are_refused(asking):
    with pytest.raises(PriceOutOfRangeError):
        check_price(asking, 250_000)


def test_an_active_listing_may_not_get_dearer():
    with pytest.raises(PriceIncreaseError):
        check_price(200_001, 250_000, current=200_000, status=ListingStatus.ACTIVE)
    check_price(200_000, 250_000, current=200_000, status=ListingStatus.ACTIVE)  # equal is fine
    check_price(240_000, 250_000, current=200_000, status=ListingStatus.DRAFT)  # only ACTIVE


NOW = datetime(2027, 1, 1, tzinfo=UTC)
START = NOW + timedelta(days=10)


@pytest.mark.parametrize("expires_at", [NOW + timedelta(seconds=1), START])
def test_expiry_between_now_and_the_event_start_passes(expires_at):
    check_expiry(expires_at, START, NOW)


@pytest.mark.parametrize("expires_at", [NOW, START + timedelta(seconds=1)])
def test_expiry_at_now_or_after_the_start_is_refused(expires_at):
    with pytest.raises(BadExpiryError):
        check_expiry(expires_at, START, NOW)
