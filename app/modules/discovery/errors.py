"""The errors discovery code raises. Each becomes a problem+json response with its own `type`."""

from app.core.exceptions import AppError


class BadPriceRangeError(AppError):
    """An empty page here would read as "nothing on sale at that price"."""

    status = 422
    code = "bad-price-range"
    title = "The lowest price is above the highest"
