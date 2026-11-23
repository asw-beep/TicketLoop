"""Two requests change the same listing at the same moment. The requests really commit.

An edit's UPDATE repeats the status, price and expiry it read in its WHERE clause, so two price
cuts that both read ₹2,000 cannot both land: the second finds the row changed and gets 409
listing-changed. Without that, both would pass their check against ₹2,000 and whichever wrote
last would win, possibly raising the price the first one had just cut.

Whether a round is a real race depends on timing. When the requests happen not to overlap,
the outcome is that of two edits one after the other, which is also correct; each round checks
the outcome is one of the correct ones, and the test requires at least one real race.
"""

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.identity.models import UserRole
from app.modules.listings.models import Listing
from scripts.make_admin import set_role
from tests.integration.api import bearer, problem_code
from tests.integration.committed import committed_rows, committing_api  # noqa: F401
from tests.integration.conftest import new_account
from tests.integration.factories import make_event, make_listing, make_verified_ticket

pytestmark = pytest.mark.integration

ROUNDS = 10
CUTS = (190_000, 180_000)


@dataclass(frozen=True)
class Outcome:
    price: int
    status: int
    code: str | None


def cut_twice_at_once(
    api: TestClient, pool: ThreadPoolExecutor, token: str, listing_id: uuid.UUID
) -> list[Outcome]:
    start = threading.Barrier(2)

    def attempt(price: int) -> Outcome:
        start.wait()  # release both requests together
        response = api.patch(
            f"/api/v1/listings/{listing_id}",
            json={"asking_price_paise": price},
            headers=bearer(token),
        )
        code = problem_code(response) if response.status_code != 200 else None
        return Outcome(price, response.status_code, code)

    return list(pool.map(attempt, CUTS))


def active_listing(db: Session, seller_id: str) -> Listing:
    ticket = make_verified_ticket(
        db,
        event_id=make_event(db, status="PUBLISHED").id,
        original_owner_id=seller_id,
        current_owner_id=seller_id,
    )
    listing = make_listing(db, ticket=ticket, status="ACTIVE", asking_price_paise=200_000)
    db.commit()
    return listing


def test_two_price_cuts_at_once_one_wins(
    committing_api: TestClient,  # noqa: F811
    committed_rows: Session,  # noqa: F811
) -> None:
    seller = new_account(committing_api)
    raced = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(ROUNDS):
            listing = active_listing(committed_rows, seller["id"])

            outcomes = cut_twice_at_once(committing_api, pool, seller["token"], listing.id)

            committed_rows.expire_all()
            final = committed_rows.get(Listing, listing.id).asking_price_paise
            changes = committed_rows.scalar(
                select(func.count()).where(
                    AuditLog.entity_id == listing.id, AuditLog.action == "listing.price_changed"
                )
            )
            winners = [o for o in outcomes if o.status == 200]
            assert changes == len(winners)
            if len(winners) == 2:
                # One after the other, ₹1,900 first: the later, lower cut stands.
                assert final == 180_000
                continue
            (winner,) = winners
            (loser,) = [o for o in outcomes if o.status != 200]
            assert final == winner.price
            assert loser.status == 409
            # listing-changed: a real race. price-increase-not-allowed: ₹1,800 went first.
            assert loser.code in {"listing-changed", "price-increase-not-allowed"}
            raced += loser.code == "listing-changed"

    # Guard against a test that passes only because the requests ran one after the other.
    assert raced > 0


def admin_account(api: TestClient, db: Session) -> dict[str, str]:
    account = new_account(api)
    set_role(db, account["email"], UserRole.ADMIN)
    db.commit()
    return account


def pending_listing(db: Session, seller_id: str) -> Listing:
    listing = active_listing(db, seller_id)
    listing.status = "PENDING_VERIFICATION"
    db.commit()
    return listing


def at_once(pool: ThreadPoolExecutor, *calls: Callable[[], Any]) -> list[Any]:
    start = threading.Barrier(len(calls))

    def run(call: Callable[[], Any]) -> Any:
        start.wait()  # release every request together
        return call()

    return list(pool.map(run, calls))


def audit_actions(db: Session, listing_id: uuid.UUID) -> list[str]:
    return list(
        db.scalars(
            select(AuditLog.action)
            .where(AuditLog.entity_type == "listing", AuditLog.entity_id == listing_id)
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    )


def test_two_approvals_at_once_one_wins(
    committing_api: TestClient,  # noqa: F811
    committed_rows: Session,  # noqa: F811
) -> None:
    seller = new_account(committing_api)
    admins = [admin_account(committing_api, committed_rows) for _ in range(2)]
    raced = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(ROUNDS):
            listing = pending_listing(committed_rows, seller["id"])
            url = f"/api/v1/admin/listings/{listing.id}/approve"

            responses = at_once(
                pool,
                *[partial(committing_api.post, url, headers=bearer(a["token"])) for a in admins],
            )

            assert sorted(r.status_code for r in responses) == [200, 409]
            (loser,) = [r for r in responses if r.status_code == 409]
            # listing-changed: a real race. listing-not-pending: the other had committed.
            assert problem_code(loser) in {"listing-changed", "listing-not-pending"}
            raced += problem_code(loser) == "listing-changed"
            assert audit_actions(committed_rows, listing.id) == ["listing.approved"]

    assert raced > 0


def test_approve_and_cancel_at_once_exactly_one_wins(
    committing_api: TestClient,  # noqa: F811
    committed_rows: Session,  # noqa: F811
) -> None:
    seller = new_account(committing_api)
    admin = admin_account(committing_api, committed_rows)
    raced = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(ROUNDS):
            listing = pending_listing(committed_rows, seller["id"])
            approve_url = f"/api/v1/admin/listings/{listing.id}/approve"
            cancel_url = f"/api/v1/listings/{listing.id}"

            approved, cancelled = at_once(
                pool,
                partial(committing_api.post, approve_url, headers=bearer(admin["token"])),
                partial(committing_api.delete, cancel_url, headers=bearer(seller["token"])),
            )

            committed_rows.expire_all()
            final = committed_rows.get(Listing, listing.id).status
            actions = audit_actions(committed_rows, listing.id)
            if approved.status_code == cancelled.status_code == 200:
                # One after the other: approved first, then the seller cancelled the ACTIVE
                # listing, which the seller may. Never the reverse order.
                assert actions == ["listing.approved", "listing.cancelled"]
                assert final == "CANCELLED"
                continue
            winner = "ACTIVE" if approved.status_code == 200 else "CANCELLED"
            loser = cancelled if winner == "ACTIVE" else approved
            assert loser.status_code == 409
            assert final == winner
            assert actions == ["listing.approved" if winner == "ACTIVE" else "listing.cancelled"]
            raced += problem_code(loser) == "listing-changed"

    assert raced > 0
