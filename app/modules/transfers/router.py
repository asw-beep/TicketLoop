"""HTTP routes for transfers. Thin: validate, call the service, shape."""

import uuid

from fastapi import APIRouter

from app.infrastructure.database.session import DbSession
from app.modules.identity.dependencies import CurrentUser
from app.modules.identity.models import UserRole
from app.modules.transfers import service
from app.modules.transfers.schemas import TransferOut

transfers_router = APIRouter(prefix="/transfers", tags=["transfers"])


@transfers_router.get("/{transfer_id}", response_model=TransferOut)
def get_transfer(transfer_id: uuid.UUID, user: CurrentUser, db: DbSession) -> TransferOut:
    """One transfer, for its buyer, its seller and admins. Why a try failed is for admins."""
    transfer = service.get_transfer_for(db, transfer_id, user)
    out = TransferOut.model_validate(transfer)
    return out if user.role == UserRole.ADMIN else out.model_copy(update={"last_error": None})
