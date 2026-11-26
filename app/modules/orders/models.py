"""Orders: one buyer's attempt to buy one held ticket.

An order is made from a reservation, at most one per reservation. What it costs is copied from
the listing when it is made, by the server: no price ever comes from a client.
"""

import uuid
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import CHAR
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from app.infrastructure.database.base import Base, Timestamps, UUIDPrimaryKey, one_of
from app.modules.listings.models import Listing
from app.modules.reservations.models import Reservation

CURRENCY = "INR"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAID = "PAID"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    CANCELLED = "CANCELLED"
    # From M9: the paid-for ticket on its way to the buyer.
    TRANSFER_PENDING = "TRANSFER_PENDING"
    COMPLETED = "COMPLETED"
    TRANSFER_FAILED = "TRANSFER_FAILED"


class Order(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(one_of("status", OrderStatus), name="status"),
        CheckConstraint("amount_paise > 0", name="amount"),
        CheckConstraint(f"currency = '{CURRENCY}'", name="currency"),
        CheckConstraint("buyer_id <> seller_id", name="not_own_listing"),
        # The second layer against duplicate orders, under the Idempotency-Key.
        UniqueConstraint("reservation_id", name="uq_orders_reservation"),
        # One person's purchases, and one person's sales, newest first.
        Index("ix_orders_buyer", "buyer_id", "created_at", "id"),
        Index("ix_orders_seller", "seller_id", "created_at", "id"),
        # Postgres does not index foreign keys.
        Index("ix_orders_listing", "listing_id"),
    )

    reservation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reservations.id"))
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id"))
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=CURRENCY)
    status: Mapped[str] = mapped_column(server_default=OrderStatus.CREATED)
    idempotency_key: Mapped[str]  # for tracing; the uniqueness is above

    # Joined eagerly: every response shows what was bought. A SELECT ... FOR UPDATE here must
    # say `with_for_update(of=Order)`.
    listing: Mapped[Listing] = relationship(lazy="joined", innerjoin=True)


# A reservation's order, if it has one, so a hold's page can lead to it. Here and not in
# reservations/models.py, which must not know about orders.
Reservation.order_id = column_property(
    select(Order.id).where(Order.reservation_id == Reservation.id).scalar_subquery()
)
