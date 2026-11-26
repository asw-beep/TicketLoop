"""M7's "done when": 100 buyers, one listing, four worker processes, exactly one reservation.

RACE_ROUNDS sets how many times (3 by default, 20 for the recorded proof):

    RACE_ROUNDS=20 uv run pytest tests/concurrency/test_reservation_race.py

A race test cannot prove there is no race: it can only fail to find one. So every round checks
the outcome in the database, and the duels give one side a head start that grows from nothing,
so that both outcomes must turn up.

The partial unique index would hide a missing condition in the claiming UPDATE from the first
test. The double click and the seller's cancel would not pass without it.
"""

import os
import random
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx2 as httpx
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import create_access_token
from app.modules.events.models import Event
from app.modules.identity.models import UserRole
from app.modules.listings.models import Listing
from app.modules.reservations.models import Reservation
from app.modules.reservations.service import expire_reservations
from tests.concurrency.harness import (
    ApiServer,
    Buyer,
    after,
    audit_counts,
    fire_together,
    make_active_listing,
    make_buyers,
    race,
    reserve,
)
from tests.integration.factories import (
    make_event,
    make_listing,
    make_reservation,
    make_user,
    make_verified_ticket,
)

pytestmark = pytest.mark.integration

ROUNDS = int(os.environ.get("RACE_ROUNDS", "3"))
DUEL_ROUNDS = 10


@pytest.mark.parametrize("round_", range(ROUNDS))
def test_a_hundred_buyers_one_winner(
    api_server: ApiServer, race_engine: Engine, buyers: list[Buyer], round_: int
) -> None:
    race(api_server.urls, race_engine, buyers)


@pytest.mark.parametrize("strategy", ["row_lock", "serializable"])
def test_the_compared_strategies_are_correct_too(
    race_settings: Settings, race_engine: Engine, buyers: list[Buyer], strategy: str
) -> None:
    """A comparison with a strategy that oversells would be meaningless."""
    with ApiServer(
        race_settings,
        app="tests.concurrency.strategy_app:app",
        env={"RESERVE_STRATEGY": strategy},
    ) as server:
        race(server.urls, race_engine, buyers)


def test_one_buyer_clicking_ten_times_gets_one_hold(
    api_server: ApiServer, race_engine: Engine, buyers: list[Buyer]
) -> None:
    for _ in range(DUEL_ROUNDS):
        listing = make_active_listing(race_engine)

        replies = fire_together(api_server.urls, [reserve(buyers[0], listing.id)] * 10)

        assert Counter(r.status for r in replies) == {201: 1, 200: 9}
        assert len({r.body["id"] for r in replies}) == 1
        with Session(race_engine) as session:
            assert audit_counts(session, listing.id) == {"listing.reserved": 1}


def test_the_seller_cancels_while_buyers_reserve(
    api_server: ApiServer, race_engine: Engine, race_settings: Settings, buyers: list[Buyer]
) -> None:
    (seller,) = make_buyers(race_engine, race_settings, 1)
    outcomes: Counter[str] = Counter()
    for round_ in range(DUEL_ROUNDS):
        listing = make_active_listing(race_engine, seller.id)

        def cancel(client: httpx.Client, listing: Listing = listing) -> httpx.Response:
            return client.delete(f"/api/v1/listings/{listing.id}", headers=seller.headers)

        cancelled, *reserved = fire_together(
            api_server.urls,
            [cancel, *(after(round_ * 0.025, reserve(b, listing.id)) for b in buyers[:20])],
        )

        winners = [r for r in reserved if r.status == 201]
        with Session(race_engine) as session:
            final = session.get_one(Listing, listing.id).status
            counts = audit_counts(session, listing.id)
            rows = session.scalars(
                select(Reservation).where(Reservation.listing_id == listing.id)
            ).all()
        if cancelled.status == 200:
            assert (final, len(winners), len(rows)) == ("CANCELLED", 0, 0)
            assert counts == {"listing.cancelled": 1}
            assert {r.status for r in reserved} <= {404, 409}
        else:
            assert (cancelled.status, final) == (409, "RESERVED")
            assert (len(winners), len(rows)) == (1, 1)
            assert counts == {"listing.reserved": 1}
        outcomes[final] += 1
    assert set(outcomes) == {"RESERVED", "CANCELLED"}, f"only one side ever won: {outcomes}"


def test_an_event_cancel_against_a_reserve(
    api_server: ApiServer, race_engine: Engine, race_settings: Settings, buyers: list[Buyer]
) -> None:
    """Never a cancelled event with a reserved ticket, whichever goes first."""
    with Session(race_engine) as session, session.begin():
        admin_id = make_user(session, role=UserRole.ADMIN).id
    headers = {"Authorization": f"Bearer {create_access_token(admin_id, race_settings)}"}
    outcomes: Counter[str] = Counter()
    for round_ in range(DUEL_ROUNDS):
        listing = make_active_listing(race_engine)

        def cancel(client: httpx.Client, listing: Listing = listing) -> httpx.Response:
            return client.patch(
                f"/api/v1/admin/events/{listing.event_id}",
                json={"status": "CANCELLED"},
                headers=headers,
            )

        cancelled, reserved = fire_together(
            api_server.urls,
            [cancel, after(round_ * 0.025, reserve(buyers[round_], listing.id))],
        )

        with Session(race_engine) as session:
            event = session.get_one(Event, listing.event_id).status
            final = session.get_one(Listing, listing.id).status
        assert (cancelled.status, reserved.status, event, final) in {
            (200, 404, "CANCELLED", "CANCELLED"),
            (409, 201, "PUBLISHED", "RESERVED"),
        }
        outcomes[event] += 1
    assert set(outcomes) == {"CANCELLED", "PUBLISHED"}, f"only one side ever won: {outcomes}"


def make_held_event(engine: Engine, holds: int) -> tuple[object, datetime]:
    """An event whose tickets are all held by buyers whose time is up, in no particular order."""
    now = datetime.now(UTC)
    starts_at = now + timedelta(days=30)
    with Session(engine) as session, session.begin():
        event = make_event(session, status="PUBLISHED", starts_at=starts_at)
        for _ in range(holds):
            seller = make_user(session).id
            ticket = make_verified_ticket(
                session, event_id=event.id, original_owner_id=seller, current_owner_id=seller
            )
            listing = make_listing(session, ticket=ticket, status="RESERVED", expires_at=starts_at)
            ended = now - timedelta(seconds=random.randint(1, 600))
            make_reservation(session, listing=listing, expires_at=ended)
        return event.id, starts_at


@pytest.mark.parametrize("change", ["cancel", "earlier", "later"])
def test_an_expiry_pass_against_a_change_to_the_event_never_deadlocks(
    api_server: ApiServer, race_engine: Engine, race_settings: Settings, change: str
) -> None:
    """Both hold many of the event's listings at once. They must take them in the same order."""
    with Session(race_engine) as session, session.begin():
        admin_id = make_user(session, role=UserRole.ADMIN).id
    headers = {"Authorization": f"Bearer {create_access_token(admin_id, race_settings)}"}
    for round_ in range(DUEL_ROUNDS):
        event_id, starts_at = make_held_event(race_engine, 20)
        body = {
            "cancel": {"status": "CANCELLED"},
            "earlier": {"starts_at": (starts_at - timedelta(days=1)).isoformat()},
            "later": {"starts_at": (starts_at + timedelta(days=1)).isoformat()},
        }[change]

        def edit(
            client: httpx.Client, event_id: object = event_id, body: dict[str, str] = body
        ) -> httpx.Response:
            return client.patch(f"/api/v1/admin/events/{event_id}", json=body, headers=headers)

        def expire(_: httpx.Client) -> httpx.Response:
            with Session(race_engine) as session, session.begin():
                found, expired = expire_reservations(session)
            return httpx.Response(200, json={"found": found, "expired": expired})

        edited, expired = fire_together(
            api_server.urls, [after(round_ * 0.005, edit), after(0.02, expire)]
        )

        assert expired.body == {"found": 20, "expired": 20}
        # A cancel is refused while any ticket is still held.
        assert edited.status in ({200, 409} if change == "cancel" else {200}), edited.body


def make_due_holds(engine: Engine, count: int) -> list[tuple[object, object]]:
    past = datetime.now(UTC) - timedelta(minutes=1)
    with Session(engine) as session, session.begin():
        made = [make_reservation(session, expires_at=past) for _ in range(count)]
        return [(r.id, r.listing_id) for r in made]


def test_two_expiry_workers_expire_each_hold_once(race_engine: Engine) -> None:
    holds = make_due_holds(race_engine, 60)
    start = threading.Barrier(2)

    def work() -> int:
        start.wait()
        total = 0
        while True:
            with Session(race_engine) as session, session.begin():
                found, expired = expire_reservations(session, limit=10)
            total += expired
            if found == 0:
                return total

    with ThreadPoolExecutor(max_workers=2) as pool:
        totals = [f.result() for f in [pool.submit(work), pool.submit(work)]]

    assert sum(totals) == 60
    assert min(totals) > 0, f"one worker did everything, so they never met: {totals}"
    with Session(race_engine) as session:
        for reservation_id, listing_id in holds:
            assert audit_counts(session, reservation_id) == {"reservation.expired": 1}
            assert audit_counts(session, listing_id) == {"listing.released": 1}
            assert session.get_one(Listing, listing_id).status == "ACTIVE"


def test_a_cancel_against_the_expiry_worker_releases_once(
    api_server: ApiServer, race_engine: Engine, race_settings: Settings
) -> None:
    (buyer,) = make_buyers(race_engine, race_settings, 1)
    outcomes: Counter[str] = Counter()
    for round_ in range(DUEL_ROUNDS):
        with Session(race_engine) as session, session.begin():
            hold = make_reservation(
                session, buyer_id=buyer.id, expires_at=datetime.now(UTC) - timedelta(minutes=1)
            )
            reservation_id, listing_id = hold.id, hold.listing_id

        def expire(_: httpx.Client) -> httpx.Response:
            with Session(race_engine) as session, session.begin():
                expire_reservations(session)
            return httpx.Response(200, json={})

        def cancel(client: httpx.Client, reservation_id: object = reservation_id) -> httpx.Response:
            return client.post(
                f"/api/v1/reservations/{reservation_id}/cancel", headers=buyer.headers
            )

        cancelled, _ = fire_together(api_server.urls, [cancel, after(round_ * 0.025, expire)])

        with Session(race_engine) as session:
            final = session.get_one(Reservation, reservation_id).status
            assert final == ("CANCELLED" if cancelled.status == 200 else "EXPIRED")
            assert sum(audit_counts(session, reservation_id).values()) == 1
            assert audit_counts(session, listing_id) == {"listing.released": 1}
            assert session.get_one(Listing, listing_id).status == "ACTIVE"
        outcomes[final] += 1
    assert set(outcomes) == {"CANCELLED", "EXPIRED"}, f"only one side ever won: {outcomes}"
