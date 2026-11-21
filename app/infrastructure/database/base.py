"""The declarative base every model inherits from, plus the columns most tables share.

Conventions from docs/database-design.md, enforced here once instead of in every model:
- Postgres generates every id (`gen_random_uuid()`) and every timestamp (`now()`).
- Python `str` maps to TEXT and `datetime` to TIMESTAMPTZ, so no column is VARCHAR(n) by
  accident and no timestamp silently drops its time zone.
- Constraint names follow pk_/uq_/ix_/ck_/fk_, so an error message names the rule that fired.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    # A CHECK is declared with a short name ("status") and becomes "ck_listings_status".
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {
        str: Text,
        datetime: DateTime(timezone=True),
        uuid.UUID: Uuid,
        dict[str, Any]: JSONB,
    }

    # Read server-generated values (ids, defaults, updated_at) back with RETURNING on INSERT
    # and UPDATE. Without it, updated_at would be expired after an update and fetched lazily,
    # which fails once get_db has closed the session and the response is being serialised.
    __mapper_args__ = {"eager_defaults": True}


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()"), sort_order=-100
    )


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)


class Timestamps(CreatedAt):
    # onupdate: every UPDATE the ORM or a Core update() sends sets updated_at = now(), unless
    # the statement sets it itself. One rule in one place, instead of a trigger per table.
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), sort_order=101
    )


def one_of(column: str, values: Iterable[str]) -> str:
    """SQL for a CHECK that a TEXT column holds one of `values`: status IN ('A', 'B')."""
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"
