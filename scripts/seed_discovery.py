"""Fill a performance database with 2,000 events and 100,000 listings.

    DATABASE_URL=postgresql+psycopg://ticketloop:ticketloop@localhost:5432/ticketloop_perf \
        uv run python -m scripts.seed_discovery --reset

`--reset` drops the database, creates it again and migrates it first. Refuses to touch a
database whose name does not end in `_perf`, so it can never flood the development one.

The same --seed gives the same data, ids included. Rows go in with COPY, which is how 200,000
rows take seconds instead of minutes. Buyers for the load test are made here too, all with the
password PASSWORD below.
"""

import argparse
import random
import sys
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from faker import Faker
from sqlalchemy import Engine, make_url, text

from app.core.config import get_settings
from app.core.security import hash_password
from app.infrastructure.database.engine import create_db_engine
from app.modules.events.models import EventCategory
from scripts.databases import is_throwaway, recreate_database, upgrade

EVENTS = 2_000
LISTINGS = 100_000
SELLERS = 2_000
BUYERS = 1_000
PASSWORD = "load test password 123"
BUYER_EMAIL = "buyer-{:04d}@perf.example.in"

CITIES = {
    "Mumbai": 20,
    "Bengaluru": 18,
    "Delhi": 16,
    "Chennai": 11,
    "Hyderabad": 10,
    "Kolkata": 8,
    "Pune": 7,
    "Kochi": 5,
    "Goa": 3,
    "Shillong": 2,
}
TICKET_CATEGORIES = {"GENERAL": 55, "SILVER": 20, "GOLD": 14, "VIP": 8, "BACKSTAGE": 3}
LISTING_STATUSES = {
    "ACTIVE": 80,
    "SOLD": 9,
    "EXPIRED": 4,
    "CANCELLED": 3,
    "PENDING_VERIFICATION": 2,
    "DRAFT": 1,
    "REJECTED": 1,
}
EVENT_WORDS = {
    "CONCERT": ("Live", "Unplugged", "World Tour", "In Concert", "Festival"),
    "SPORTS": ("Derby", "Final", "League Night", "Championship", "Cup"),
    "COLLEGE": ("Fest", "Cultural Night", "Tech Fest", "Pro Show", "Battle of Bands"),
    "OTHER": ("Comedy Night", "Expo", "Conference", "Theatre Evening", "Food Fair"),
}


class Seeder:
    def __init__(self, seed: int, now: datetime) -> None:
        self.random = random.Random(seed)
        self.fake = Faker("en_IN")
        self.fake.seed_instance(seed)
        self.now = now
        self.provider_id = self.uuid()
        self.events: list[tuple[uuid.UUID, datetime]] = []
        self.sellers: list[uuid.UUID] = []
        self.tickets: list[tuple[Any, ...]] = []
        self.listings: list[tuple[Any, ...]] = []

    def uuid(self) -> uuid.UUID:
        return uuid.UUID(int=self.random.getrandbits(128), version=4)

    def pick(self, weights: dict[str, int]) -> str:
        return self.random.choices(list(weights), list(weights.values()))[0]

    def users(self, password_hash: str) -> Iterator[tuple[Any, ...]]:
        for i in range(SELLERS):
            self.sellers.append(self.uuid())
            yield (self.sellers[-1], f"seller-{i:04d}@perf.example.in", password_hash, self.name())
        for i in range(BUYERS):
            yield (self.uuid(), BUYER_EMAIL.format(i), password_hash, self.name())

    def name(self) -> str:
        return self.fake.name()

    def event_rows(self) -> Iterator[tuple[Any, ...]]:
        for _ in range(EVENTS):
            event_id = self.uuid()
            category = self.random.choice(list(EventCategory))
            name = f"{self.fake.last_name()} {self.random.choice(EVENT_WORDS[category])}"
            starts_at = self.now + timedelta(days=self.random.uniform(2, 300))
            self.events.append((event_id, starts_at))
            yield (
                event_id,
                name,
                self.fake.sentence(nb_words=12),
                category.value,
                f"{self.fake.last_name()} Arena",
                self.pick(CITIES),
                starts_at,
                self.fake.company(),
                self.provider_id,
                "PUBLISHED",
            )

    def make_tickets_and_listings(self) -> None:
        for i in range(LISTINGS):
            # A few events are far more popular than the rest, as in life.
            position = min(int(self.random.expovariate(1 / 350)), EVENTS - 1)
            event_id, starts_at = self.events[position]
            seller = self.random.choice(self.sellers)
            ticket_id = self.uuid()
            price = self.random.randrange(50_000, 2_000_000, 5_000)
            asking = max(100, int(price * self.random.uniform(0.4, 1.0)) // 5_000 * 5_000)
            created = self.now - timedelta(days=self.random.uniform(0.1, 90))
            status = self.pick(LISTING_STATUSES)
            expires = starts_at if status != "EXPIRED" else created + timedelta(days=1)
            self.tickets.append(
                (
                    ticket_id,
                    event_id,
                    seller,
                    seller,
                    self.pick(TICKET_CATEGORIES),
                    f"Block {self.random.choice('ABCDEF')}, row {self.random.randint(1, 40)}",
                    price,
                    f"ORD-{i:06d}",
                    f"TKT-{i:06d}",
                    "VERIFIED",
                    True,
                    created,
                )
            )
            self.listings.append(
                (
                    self.uuid(),
                    ticket_id,
                    event_id,
                    seller,
                    asking,
                    price,
                    status,
                    expires,
                    "Seat does not match." if status == "REJECTED" else None,
                    created,
                )
            )


def copy(cursor: psycopg.Cursor, table: str, columns: str, rows: Any) -> None:
    with cursor.copy(f"COPY {table} ({columns}) FROM STDIN") as out:
        for row in rows:
            out.write_row(row)


def seed(engine: Engine, number: int) -> None:
    started = time.perf_counter()
    seeder = Seeder(number, datetime.now(UTC).replace(microsecond=0))
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM listings")
            if cursor.fetchone()[0]:
                raise SystemExit("This database already has listings. Use --reset to start again.")
            cursor.execute(
                "INSERT INTO ticket_providers (id, code, name) VALUES (%s, 'perfprovider', 'Perf')",
                (seeder.provider_id,),
            )
            copy(
                cursor,
                "users",
                "id, email, password_hash, full_name",
                seeder.users(hash_password(PASSWORD)),
            )
            copy(
                cursor,
                "events",
                "id, name, description, category, venue, city, starts_at, organizer, "
                "provider_id, status",
                seeder.event_rows(),
            )
            seeder.make_tickets_and_listings()
            copy(
                cursor,
                "tickets",
                "id, event_id, original_owner_id, current_owner_id, ticket_category, "
                "seat_details, original_price_paise, purchase_reference, provider_reference, "
                "verification_status, transfer_eligible, created_at",
                seeder.tickets,
            )
            copy(
                cursor,
                "listings",
                "id, ticket_id, event_id, seller_id, asking_price_paise, original_price_paise, "
                "status, expires_at, rejection_reason, created_at",
                seeder.listings,
            )
        raw.commit()
    finally:
        raw.close()
    # Without fresh statistics the planner guesses, and the plans mean nothing. VACUUM also sets
    # the visibility map, as autovacuum would in time.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM ANALYZE"))
    print(
        f"Seeded {EVENTS:,} events, {LISTINGS:,} listings, {SELLERS:,} sellers and "
        f"{BUYERS:,} buyers in {time.perf_counter() - started:.1f}s."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="drop, create and migrate it first")
    parser.add_argument("--seed", type=int, default=20261125, help="same seed, same data")
    args = parser.parse_args(argv)

    settings = get_settings()
    name = make_url(settings.database_url).database
    if not is_throwaway(name, ("_perf",)):
        print(f"Refusing: {name!r} does not end in _perf. Set DATABASE_URL.", file=sys.stderr)
        return 2
    if args.reset:
        recreate_database(settings.database_url)
    engine = create_db_engine(settings)
    try:
        if args.reset:
            upgrade(engine)
        seed(engine, args.seed)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
