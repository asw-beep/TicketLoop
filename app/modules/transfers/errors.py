"""The errors transfer code raises."""

from app.core.exceptions import ConflictError, NotFoundError


class TransferNotFoundError(NotFoundError):
    """One error for "no such transfer" and "not yours"."""

    code = "transfer-not-found"
    title = "Transfer not found"

    def __init__(self) -> None:
        super().__init__("Transfer not found.")


class IllegalTransferTransitionError(ConflictError):
    code = "illegal-transfer-transition"
    title = "The transfer cannot move to that status"


class TransferChangedError(ConflictError):
    """The transfer moved between our read and our write. Carries `current_status`."""

    code = "transfer-changed"
    title = "The transfer changed while it was being worked on"
