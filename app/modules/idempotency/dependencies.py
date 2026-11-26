"""The Idempotency-Key header, as a route parameter."""

import re
from typing import Annotated

from fastapi import Depends, Header

from app.modules.idempotency.errors import IdempotencyKeyInvalidError, IdempotencyKeyRequiredError

_KEY = re.compile(r"[\x21-\x7e]{1,255}")  # printable ASCII, no spaces


def idempotency_key(
    idempotency_key: Annotated[
        str | None,
        Header(description="Required. A new random value per click, the same one on a retry."),
    ] = None,
) -> str:
    if not idempotency_key:
        raise IdempotencyKeyRequiredError()
    if not _KEY.fullmatch(idempotency_key):
        raise IdempotencyKeyInvalidError()
    return idempotency_key


IdempotencyKey = Annotated[str, Depends(idempotency_key)]
