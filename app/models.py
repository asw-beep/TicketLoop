"""Imports every model, so Base.metadata knows every table.

Alembic's env.py and the migration tests import this module. Add each new module's models
here when it gets tables.
"""

from app.modules.audit.models import AuditLog
from app.modules.events.models import Event, TicketProvider
from app.modules.identity.models import PasswordResetToken, RefreshToken, User
from app.modules.listings.models import Listing
from app.modules.reservations.models import Reservation
from app.modules.tickets.models import Ticket, TicketDocument

__all__ = [
    "AuditLog",
    "Event",
    "Listing",
    "PasswordResetToken",
    "RefreshToken",
    "Reservation",
    "Ticket",
    "TicketDocument",
    "TicketProvider",
    "User",
]
