"""HTTP routes for /tickets and /admin/tickets. Thin: validate, call the service, shape."""

import uuid

from fastapi import APIRouter, UploadFile, status

from app.core.pagination import Cursor, Limit, Page
from app.infrastructure.database.session import DbSession
from app.infrastructure.storage import Storage
from app.modules.identity.dependencies import AdminUser, CurrentUser
from app.modules.tickets import documents, service
from app.modules.tickets.models import MAX_DOCUMENT_BYTES
from app.modules.tickets.schemas import (
    DocumentLinkOut,
    DocumentOut,
    TicketIn,
    TicketOut,
    VerifyIn,
)

tickets_router = APIRouter(prefix="/tickets", tags=["tickets"])
admin_tickets_router = APIRouter(prefix="/admin/tickets", tags=["admin: tickets"])


@tickets_router.post("", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
def register_ticket(body: TicketIn, user: CurrentUser, db: DbSession) -> TicketOut:
    """Register a ticket you own. It starts PENDING until an admin verifies it."""
    return TicketOut.model_validate(service.register_ticket(db, user, body))


# Declared before /{ticket_id}, or "me" would be read as a (malformed) ticket ID.
@tickets_router.get("/me", response_model=Page[TicketOut])
def my_tickets(
    user: CurrentUser, db: DbSession, cursor: Cursor = None, limit: Limit = 20
) -> Page[TicketOut]:
    """Your tickets, newest first."""
    items, next_cursor = service.list_my_tickets(db, user, cursor, limit)
    return Page[TicketOut](items=items, next_cursor=next_cursor)


@tickets_router.get("/{ticket_id}", response_model=TicketOut)
def get_ticket(ticket_id: uuid.UUID, user: CurrentUser, db: DbSession) -> TicketOut:
    """Your ticket, or any ticket for an admin. Anyone else gets 404."""
    return TicketOut.model_validate(service.get_visible_ticket(db, ticket_id, user))


@tickets_router.post(
    "/{ticket_id}/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED
)
def upload_document(
    ticket_id: uuid.UUID, file: UploadFile, user: CurrentUser, db: DbSession, storage: Storage
) -> DocumentOut:
    """Upload purchase evidence: a PDF, PNG or JPEG up to 5 MiB. The type is read from the
    file's bytes; its name and the browser's claimed type are ignored."""
    # One byte past the limit is enough to know it is too large; never read the rest.
    data = file.file.read(MAX_DOCUMENT_BYTES + 1)
    return DocumentOut.model_validate(documents.upload_document(db, storage, ticket_id, user, data))


@tickets_router.get("/{ticket_id}/documents", response_model=list[DocumentOut])
def list_documents(ticket_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[DocumentOut]:
    return [DocumentOut.model_validate(d) for d in documents.list_documents(db, ticket_id, user)]


@tickets_router.get("/{ticket_id}/documents/{document_id}", response_model=DocumentLinkOut)
def document_link(
    ticket_id: uuid.UUID,
    document_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> DocumentLinkOut:
    """A link that opens the file for 5 minutes."""
    url, expires_at = documents.document_link(db, storage, ticket_id, document_id, user)
    return DocumentLinkOut(url=url, expires_at=expires_at)


@admin_tickets_router.get("/pending", response_model=Page[TicketOut])
def pending_tickets(
    admin: AdminUser, db: DbSession, cursor: Cursor = None, limit: Limit = 20
) -> Page[TicketOut]:
    """Tickets waiting for review, oldest first."""
    items, next_cursor = service.list_pending_tickets(db, cursor, limit)
    return Page[TicketOut](items=items, next_cursor=next_cursor)


@admin_tickets_router.post("/{ticket_id}/verify", response_model=TicketOut)
def verify_ticket(
    ticket_id: uuid.UUID, body: VerifyIn, admin: AdminUser, db: DbSession
) -> TicketOut:
    """Mark a PENDING ticket VERIFIED or REJECTED. A rejection needs a note for the owner."""
    return TicketOut.model_validate(service.verify_ticket(db, ticket_id, admin, body))
