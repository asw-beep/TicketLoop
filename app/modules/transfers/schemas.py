"""Response bodies for transfers."""

import uuid

from pydantic import AliasChoices, AwareDatetime, Field

from app.core.schemas import ResponseModel
from app.modules.transfers.models import TransferStatus


class TransferSummaryOut(ResponseModel):
    """A transfer as its buyer and seller see it, inside their order: how far the ticket has
    got. Never why a try failed; that is for admins."""

    id: uuid.UUID
    order_id: uuid.UUID
    status: TransferStatus
    attempt_count: int
    initiated_at: AwareDatetime = Field(validation_alias=AliasChoices("initiated_at", "created_at"))
    completed_at: AwareDatetime | None


class TransferOut(TransferSummaryOut):
    """`Transfer` in the API contract. `last_error` is null for anyone but an admin."""

    last_error: str | None = None
