"""fakepay's memory: one SQLite table of payment intents."""

import sqlite3
import threading
from dataclasses import dataclass
from typing import Literal

Settled = Literal["succeeded", "failed"]
Status = Literal["open", "succeeded", "failed", "expired"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS intents (
    transaction_id  TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    amount_paise    INTEGER NOT NULL,
    currency        TEXT NOT NULL,
    return_url      TEXT NOT NULL,
    settled         TEXT CHECK (settled IN ('succeeded', 'failed')),
    created_at      REAL NOT NULL,
    expires_at      REAL NOT NULL
)
"""


@dataclass(frozen=True)
class Intent:
    transaction_id: str
    idempotency_key: str
    amount_paise: int
    currency: str
    return_url: str
    settled: Settled | None
    created_at: float
    expires_at: float

    def status(self, now: float) -> Status:
        if self.settled:
            return self.settled
        return "expired" if self.expires_at <= now else "open"


class Store:
    """One connection behind a lock: SQLite writes one at a time anyway."""

    def __init__(self, path: str) -> None:
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = NORMAL")
            self._db.execute(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add(self, intent: Intent) -> Intent:
        """Store it, or return the one already stored under this idempotency key."""
        with self._lock:
            self._db.execute(
                "INSERT INTO intents VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (idempotency_key) DO NOTHING",
                (
                    intent.transaction_id,
                    intent.idempotency_key,
                    intent.amount_paise,
                    intent.currency,
                    intent.return_url,
                    intent.settled,
                    intent.created_at,
                    intent.expires_at,
                ),
            )
            row = self._db.execute(
                "SELECT * FROM intents WHERE idempotency_key = ?", (intent.idempotency_key,)
            ).fetchone()
        return Intent(**row)

    def get(self, transaction_id: str) -> Intent | None:
        return self._one("transaction_id", transaction_id)

    def by_key(self, idempotency_key: str) -> Intent | None:
        return self._one("idempotency_key", idempotency_key)

    def settle(self, transaction_id: str, outcome: Settled, now: float) -> Intent | None:
        """Settle an open intent. None if it is unknown, settled already, or expired."""
        with self._lock:
            row = self._db.execute(
                "UPDATE intents SET settled = ?"
                " WHERE transaction_id = ? AND settled IS NULL AND expires_at > ? RETURNING *",
                (outcome, transaction_id, now),
            ).fetchone()
        return Intent(**row) if row else None

    def _one(self, column: Literal["transaction_id", "idempotency_key"], value: str):
        with self._lock:
            row = self._db.execute(
                f"SELECT * FROM intents WHERE {column} = ?",  # noqa: S608 - one of two literals
                (value,),
            ).fetchone()
        return Intent(**row) if row else None
