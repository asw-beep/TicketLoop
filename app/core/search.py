"""Text people type into a search box, made safe to put in a query."""

from typing import Annotated

from pydantic import AfterValidator, StringConstraints


def _no_nul(value: str) -> str:
    # Postgres text cannot hold a NUL, so "%00" in a query string would be a 500, not a 422.
    if "\x00" in value:
        raise ValueError("text cannot contain a NUL character")
    return value


# Trimmed. Blank is allowed and means "no filter": a search box someone cleared.
SearchText = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=100), AfterValidator(_no_nul)
]

# ILIKE treats % and _ as wildcards; this character makes them mean themselves.
LIKE_ESCAPE = "\\"


def contains(text: str) -> str:
    """An ILIKE pattern for "contains this text", taken literally. Use it with LIKE_ESCAPE:
    `column.ilike(contains(text), escape=LIKE_ESCAPE)`."""
    for special in (LIKE_ESCAPE, "%", "_"):
        text = text.replace(special, LIKE_ESCAPE + special)
    return f"%{text}%"
