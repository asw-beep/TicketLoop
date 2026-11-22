"""Purchase evidence: upload, list and open (FR-03). Functions never commit.

Rules, in the order they are checked:
1. The ticket must be visible to the caller (owner or admin), else the same 404 as always.
2. Only the owner uploads (an admin gets 403), only while the ticket is PENDING, and at most
   MAX_DOCUMENTS_PER_TICKET files.
3. The file is 1 byte to 5 MiB, and its first bytes say PDF, PNG or JPEG. The filename and
   the browser's claimed type are never read.
4. The object is stored under a random key first, then the row is written. If storage fails
   there is no row; if the row fails, an unreferenced object stays behind under a random key,
   which is harmless.

The file's contents and the presigned URLs are never logged: a URL opens the file for anyone
who has it, for PRESIGN_SECONDS.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.storage import PRESIGN_SECONDS, ObjectStorage, StorageError
from app.modules.identity.models import User
from app.modules.tickets.errors import (
    DocumentNotFoundError,
    EmptyFileError,
    FileTooLargeError,
    OwnerOnlyError,
    StorageUnavailableError,
    TicketNotPendingError,
    TooManyDocumentsError,
    UnsupportedFileTypeError,
)
from app.modules.tickets.files import SNIFF_BYTES, sniff_content_type
from app.modules.tickets.models import (
    MAX_DOCUMENT_BYTES,
    MAX_DOCUMENTS_PER_TICKET,
    TicketDocument,
    VerificationStatus,
)
from app.modules.tickets.service import get_visible_ticket

KEY_PREFIX = "ticket-documents/"


def upload_document(
    db: Session, storage: ObjectStorage, ticket_id: uuid.UUID, user: User, data: bytes
) -> TicketDocument:
    ticket = get_visible_ticket(db, ticket_id, user)
    if ticket.current_owner_id != user.id:
        raise OwnerOnlyError()
    if ticket.verification_status != VerificationStatus.PENDING:
        raise TicketNotPendingError(f"The ticket is {ticket.verification_status}.")
    count = db.scalar(
        select(func.count())
        .select_from(TicketDocument)
        .where(TicketDocument.ticket_id == ticket_id)
    )
    if count >= MAX_DOCUMENTS_PER_TICKET:
        raise TooManyDocumentsError(f"A ticket can have at most {MAX_DOCUMENTS_PER_TICKET}.")

    if not data:
        raise EmptyFileError()
    if len(data) > MAX_DOCUMENT_BYTES:
        raise FileTooLargeError(f"Files are limited to {MAX_DOCUMENT_BYTES} bytes (5 MiB).")
    content_type = sniff_content_type(data[:SNIFF_BYTES])
    if content_type is None:
        raise UnsupportedFileTypeError()

    key = f"{KEY_PREFIX}{uuid.uuid4().hex}"
    try:
        storage.put_object(key, data, content_type)
    except StorageError:
        raise StorageUnavailableError() from None

    document = TicketDocument(
        ticket_id=ticket_id,
        uploaded_by=user.id,
        storage_key=key,
        content_type=content_type,
        size_bytes=len(data),
    )
    db.add(document)
    db.flush()
    db.refresh(document)
    return document


def list_documents(db: Session, ticket_id: uuid.UUID, user: User) -> list[TicketDocument]:
    get_visible_ticket(db, ticket_id, user)
    stmt = (
        select(TicketDocument)
        .where(TicketDocument.ticket_id == ticket_id)
        .order_by(TicketDocument.created_at, TicketDocument.id)
    )
    return list(db.scalars(stmt))


def document_link(
    db: Session,
    storage: ObjectStorage,
    ticket_id: uuid.UUID,
    document_id: uuid.UUID,
    user: User,
) -> tuple[str, datetime]:
    """A presigned link to the file and when it stops working."""
    get_visible_ticket(db, ticket_id, user)
    # Matched on both IDs: a document of one ticket is not reachable through another.
    document = db.scalar(
        select(TicketDocument).where(
            TicketDocument.id == document_id, TicketDocument.ticket_id == ticket_id
        )
    )
    if document is None:
        raise DocumentNotFoundError()
    expires_at = datetime.now(UTC) + timedelta(seconds=PRESIGN_SECONDS)
    url = storage.presigned_get_url(document.storage_key, document.content_type, PRESIGN_SECONDS)
    return url, expires_at
