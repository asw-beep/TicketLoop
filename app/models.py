"""Imports every model, so Base.metadata knows every table.

Alembic's env.py and the migration tests import this module. Add each new module's models
here when it gets tables.
"""

from app.modules.audit.models import AuditLog
from app.modules.events.models import Event, TicketProvider
from app.modules.idempotency.models import IdempotencyKeyRow
from app.modules.identity.models import PasswordResetToken, RefreshToken, User
from app.modules.listings.models import Listing
from app.modules.notifications.models import Notification
from app.modules.orders.models import Order
from app.modules.outbox.models import OutboxEvent, ProcessedEvent
from app.modules.payments.models import Payment, PaymentEvent, ReconciliationRun
from app.modules.reservations.models import Reservation
from app.modules.tickets.models import Ticket, TicketDocument
from app.modules.transfers.models import Transfer

__all__ = [
    "AuditLog",
    "Event",
    "IdempotencyKeyRow",
    "Listing",
    "Notification",
    "Order",
    "OutboxEvent",
    "PasswordResetToken",
    "Payment",
    "PaymentEvent",
    "ProcessedEvent",
    "ReconciliationRun",
    "RefreshToken",
    "Reservation",
    "Ticket",
    "TicketDocument",
    "TicketProvider",
    "Transfer",
    "User",
]
