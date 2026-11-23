"""Build valid rows with one call, so a test only spells out the fields it is about.

Each function adds the row, flushes it (so Postgres fills in ids and defaults, and checks
constraints) and returns it. Pass keyword arguments to override any field.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import hash_opaque_token, new_opaque_token
from app.modules.events.models import Event, TicketProvider
from app.modules.identity.models import PasswordResetToken, RefreshToken, User
from app.modules.listings.models import Listing
from app.modules.tickets.models import Ticket, TicketDocument


def _unique() -> str:
    return uuid.uuid4().hex[:12]


def _save[T](session: Session, row: T) -> T:
    session.add(row)
    session.flush()
    return row


def make_user(session: Session, **fields: Any) -> User:
    defaults: dict[str, Any] = {
        "email": f"user-{_unique()}@example.in",
        "password_hash": "not-a-real-hash",
        "full_name": "Test User",
    }
    return _save(session, User(**(defaults | fields)))


def make_refresh_token(session: Session, **fields: Any) -> RefreshToken:
    defaults: dict[str, Any] = {
        "family_id": uuid.uuid4(),
        "token_hash": hash_opaque_token(new_opaque_token()),
        "expires_at": datetime.now(UTC) + timedelta(days=7),
    }
    if "user_id" not in fields:
        defaults["user_id"] = make_user(session).id
    return _save(session, RefreshToken(**(defaults | fields)))


def make_password_reset_token(session: Session, **fields: Any) -> PasswordResetToken:
    defaults: dict[str, Any] = {
        "token_hash": hash_opaque_token(new_opaque_token()),
        "expires_at": datetime.now(UTC) + timedelta(minutes=30),
    }
    if "user_id" not in fields:
        defaults["user_id"] = make_user(session).id
    return _save(session, PasswordResetToken(**(defaults | fields)))


def make_provider(session: Session, **fields: Any) -> TicketProvider:
    code = f"provider-{_unique()}"
    return _save(session, TicketProvider(**({"code": code, "name": code} | fields)))


def make_event(session: Session, **fields: Any) -> Event:
    defaults: dict[str, Any] = {
        "name": "Test Concert",
        "category": "CONCERT",
        "venue": "Test Arena",
        "city": "Kochi",
        "starts_at": datetime.now(UTC) + timedelta(days=30),
        "organizer": "Test Organizer",
    }
    if "provider_id" not in fields:
        defaults["provider_id"] = make_provider(session).id
    return _save(session, Event(**(defaults | fields)))


def make_ticket(session: Session, **fields: Any) -> Ticket:
    defaults: dict[str, Any] = {
        "ticket_category": "GENERAL",
        "original_price_paise": 250_000,  # ₹2,500
        "purchase_reference": f"purchase-{_unique()}",
        "provider_reference": f"ref-{_unique()}",
    }
    if "event_id" not in fields:
        defaults["event_id"] = make_event(session).id
    if "original_owner_id" not in fields:
        owner = make_user(session)
        defaults["original_owner_id"] = owner.id
        defaults.setdefault("current_owner_id", owner.id)
    return _save(session, Ticket(**(defaults | fields)))


def make_verified_ticket(session: Session, **fields: Any) -> Ticket:
    """A ticket an admin has verified as transfer-eligible: one that can be listed."""
    return make_ticket(
        session, **({"verification_status": "VERIFIED", "transfer_eligible": True} | fields)
    )


def make_document(session: Session, **fields: Any) -> TicketDocument:
    defaults: dict[str, Any] = {
        "storage_key": f"ticket-documents/{uuid.uuid4().hex}",
        "content_type": "image/png",
        "size_bytes": 1024,
    }
    if "ticket_id" not in fields:
        ticket = make_ticket(session)
        defaults["ticket_id"] = ticket.id
        defaults.setdefault("uploaded_by", ticket.current_owner_id)
    elif "uploaded_by" not in fields:
        defaults["uploaded_by"] = make_user(session).id
    return _save(session, TicketDocument(**(defaults | fields)))


def make_listing(session: Session, **fields: Any) -> Listing:
    ticket = fields.pop("ticket", None) or make_ticket(session)
    defaults: dict[str, Any] = {
        "ticket_id": ticket.id,
        "event_id": ticket.event_id,
        "seller_id": ticket.current_owner_id,
        "asking_price_paise": ticket.original_price_paise,
        "original_price_paise": ticket.original_price_paise,
        "expires_at": datetime.now(UTC) + timedelta(days=29),
    }
    if fields.get("status") == "REJECTED":  # ck_listings_rejection_reason
        defaults["rejection_reason"] = "Test reason."
    return _save(session, Listing(**(defaults | fields)))
