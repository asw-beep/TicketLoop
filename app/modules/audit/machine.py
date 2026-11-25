"""One state machine, used by every entity with a lifecycle.

A machine is a table of allowed moves plus `move()`, the only function that changes a status.
`move()` checks the table, applies the change with a conditional UPDATE, and writes the audit
row in the same transaction, so a status change and its audit row commit together or not at
all. M5 wrote this for listings; M7 moved it here when reservations needed the same thing.

`move()` knows nothing about who may act. Ownership, roles and every other check live in the
service functions that call it, and each of those also names the states it acts from: the
table describes the whole machine, including moves a given route must never make.
"""

import uuid
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, func, update
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.modules.audit.service import record


class StateMachine[Row, Status: StrEnum]:
    def __init__(
        self,
        model: type[Row],
        entity_type: str,
        status: type[Status],
        allowed: Mapping[Status, frozenset[Status]],
        *,
        illegal: type[ConflictError],
        changed: type[ConflictError],
        named: Mapping[tuple[Status, Status], str] | None = None,
    ) -> None:
        """`illegal` is raised for a move the table does not have, `changed` when the row moved
        between our read and our write. `named` overrides the default audit action,
        "<entity_type>.<new status>", for the moves with a better word."""
        self.model: Any = model
        self.entity_type = entity_type
        self.status = status
        self.allowed = allowed
        self.illegal = illegal
        self.changed = changed
        self.action: dict[tuple[Status, Status], str] = {
            (from_, to): (named or {}).get((from_, to), f"{entity_type}.{to.lower()}")
            for from_, tos in allowed.items()
            for to in tos
        }

    def move(
        self,
        db: Session,
        row: Row,
        to: Status,
        *,
        actor_id: uuid.UUID | None,
        values: Mapping[str, Any] | None = None,
        details: Mapping[str, Any] | None = None,
        where: Sequence[ColumnElement[bool]] = (),
    ) -> Row:
        """Move `row` to `to`, write its audit row, and return the refreshed row.

        The status this function read is repeated in the UPDATE's WHERE clause. Checking it in
        Python and then writing an unconditional UPDATE would leave a gap between the check and
        the write where another request could change the row; with the check inside the UPDATE,
        Postgres decides atomically, and zero rows updated means someone else got there first
        (`changed`). The caller's transaction then rolls back, audit row and all.

        `values` are the other columns the move sets. `details` are added to the audit row's
        {"from", "to"}. `where` adds conditions the move must also meet at that same instant.
        """
        entity: Any = row
        seen = self.status(entity.status)
        if to not in self.allowed[seen]:
            raise self.illegal(
                f"{_a(seen)} {seen} {self.entity_type} cannot become {to}.", current_status=seen
            )
        moved = db.execute(
            update(self.model)
            .where(self.model.id == entity.id, self.model.status == seen, *where)
            .values(status=to, updated_at=func.now(), **(values or {}))
            .returning(self.model.id)
        ).scalar_one_or_none()
        if moved is None:
            db.refresh(row)
            raise self.changed(
                f"The {self.entity_type} is now {entity.status}.", current_status=entity.status
            )
        record(
            db,
            actor_id=actor_id,
            entity_type=self.entity_type,
            entity_id=entity.id,
            action=self.action[(seen, to)],
            details={"from": seen, "to": to, **(details or {})},
        )
        db.refresh(row)
        return row


def _a(word: str) -> str:
    return "An" if word[:1] in "AEIOU" else "A"
