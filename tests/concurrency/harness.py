"""Real races: a real server with several worker processes, real HTTP, real commits.

The API runs in four processes of its own, on a throwaway database. Four processes matter: a
lock held in one Python process would pass any test run inside that process and protect nothing
in production. The buyers are threads that wait at a barrier and are
released together, each with its connection already open.
"""

import logging
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import httpx2 as httpx
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.config import LOCAL_FAKEPAY_SECRET, Settings
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.listings.models import Listing
from app.modules.reservations.models import LIVE_STATUSES, Reservation
from tests.integration.factories import make_event, make_listing, make_user, make_verified_ticket

REPO_ROOT = Path(__file__).resolve().parents[2]
# One log line per request, twice a buyer a round, would bury a failure.
logging.getLogger("httpx2").setLevel(logging.WARNING)

BUYERS = 100
WORKERS = 4
Call = Callable[[httpx.Client], httpx.Response]


class ApiServer:
    """`with ApiServer(settings) as server:` the API in WORKERS processes, gone afterwards.

    One port each, rather than `uvicorn --workers` sharing a socket: on Windows some requests to
    a shared socket never got an answer, and this way each process gets exactly its share of the
    buyers. What matters is the same: four interpreters and four connection pools with nothing
    in common but Postgres.
    """

    def __init__(
        self,
        settings: Settings,
        app: str = "app.main:app",
        workers: int = WORKERS,
        env: dict[str, str] | None = None,
    ) -> None:
        self._app = app
        self._env = os.environ | {
            "DATABASE_URL": settings.database_url,
            "JWT_SECRET_KEY": settings.jwt_secret_key.get_secret_value(),
            # Never a developer's own, from .env or the environment: the tests sign with this.
            "FAKEPAY_WEBHOOK_SECRET": LOCAL_FAKEPAY_SECRET,
            "ENVIRONMENT": "test",
            "LOG_LEVEL": "WARNING",
            **(env or {}),
        }
        self._ports = [free_port() for _ in range(workers)]
        self.urls = [f"http://127.0.0.1:{port}" for port in self._ports]
        self._processes: list[subprocess.Popen[bytes]] = []

    def __enter__(self) -> Self:
        try:
            for port in self._ports:
                command = [sys.executable, "-m", "uvicorn", self._app, "--port", str(port)]
                command += ["--host", "127.0.0.1", "--log-level", "warning", "--no-access-log"]
                self._processes.append(subprocess.Popen(command, cwd=REPO_ROOT, env=self._env))
            for url, process in zip(self.urls, self._processes, strict=True):
                _wait_until_up(f"{url}/api/v1/health", process)
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        for process in self._processes:
            process.kill()
        for process in self._processes:
            process.wait(timeout=10)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_until_up(url: str, process: "subprocess.Popen[bytes]") -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"uvicorn exited with {process.returncode}")
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise RuntimeError("uvicorn did not come up in 60 seconds")


class FakepayServer:
    """`with FakepayServer(url, webhook_url):` fakepay in a process of its own."""

    def __init__(self, url: str, webhook_url: str) -> None:
        self.url = url
        self._env = os.environ | {
            "FAKEPAY_PUBLIC_URL": url,
            "FAKEPAY_WEBHOOK_URL": webhook_url,
            "FAKEPAY_WEBHOOK_SECRET": LOCAL_FAKEPAY_SECRET,  # the one the test API checks
        }

    def __enter__(self) -> Self:
        command = [sys.executable, "-m", "uvicorn", "fakepay.main:app", "--host", "127.0.0.1"]
        command += ["--port", self.url.rsplit(":", 1)[1], "--log-level", "warning"]
        self._process = subprocess.Popen(command, cwd=REPO_ROOT, env=self._env)
        try:
            _wait_until_up(f"{self.url}/health", self._process)
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        self._process.kill()
        self._process.wait(timeout=10)


@dataclass(frozen=True)
class Reply:
    status: int
    body: dict[str, object]
    milliseconds: float
    attempts: int  # how many times the server ran the transaction (strategy_app.py)
    replayed: bool  # a stored answer, not a new one (idempotency keys)


def after(seconds: float, call: Call) -> Call:
    """The same call, starting this much later than everyone else's."""

    def delayed(client: httpx.Client) -> httpx.Response:
        time.sleep(seconds)
        return call(client)

    return delayed


def fire_together(urls: Sequence[str], calls: Sequence[Call]) -> list[Reply]:
    """Run every call at the same moment, each on its own thread and open connection, spread
    over the servers in turn."""
    start = threading.Barrier(len(calls))
    limits = httpx.Limits(max_connections=None, max_keepalive_connections=None)
    # verify=False only skips loading the CA bundle: these are plain http:// requests.
    clients = [httpx.Client(base_url=u, timeout=30, limits=limits, verify=False) for u in urls]

    def run(numbered: tuple[int, Call]) -> Reply:
        number, call = numbered
        client = clients[number % len(clients)]
        try:
            client.get("/api/v1/health")  # opens this thread's connection
            start.wait(timeout=60)  # now, all together
        except BaseException:
            start.abort()  # nobody is left waiting for a thread that will not come
            raise
        began = time.perf_counter()
        response = call(client)
        took = (time.perf_counter() - began) * 1000
        attempts = int(response.headers.get("x-attempts", "1"))
        replayed = response.headers.get("idempotent-replayed") == "true"
        return Reply(response.status_code, response.json(), took, attempts, replayed)

    try:
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return list(pool.map(run, enumerate(calls)))
    finally:
        for client in clients:
            client.close()


@dataclass(frozen=True)
class Buyer:
    id: uuid.UUID
    headers: dict[str, str]


def make_buyers(engine: Engine, settings: Settings, count: int = BUYERS) -> list[Buyer]:
    """Users with tokens minted directly: the race is not about logging in."""
    with Session(engine) as session, session.begin():
        ids = [make_user(session).id for _ in range(count)]
    return [Buyer(i, {"Authorization": f"Bearer {create_access_token(i, settings)}"}) for i in ids]


def make_active_listing(engine: Engine, seller_id: uuid.UUID | None = None) -> Listing:
    with Session(engine, expire_on_commit=False) as session, session.begin():
        seller = seller_id or make_user(session).id
        ticket = make_verified_ticket(
            session,
            event_id=make_event(session, status="PUBLISHED").id,
            original_owner_id=seller,
            current_owner_id=seller,
        )
        return make_listing(session, ticket=ticket, status="ACTIVE")


def reserve(buyer: Buyer, listing_id: uuid.UUID) -> Call:
    path = f"/api/v1/listings/{listing_id}/reservations"
    return lambda client: client.post(path, headers=buyer.headers)


def race(urls: Sequence[str], engine: Engine, buyers: Sequence[Buyer]) -> list[Reply]:
    """Every buyer reserves one new listing at the same moment. Checks the outcome."""
    listing = make_active_listing(engine)
    replies = fire_together(urls, [reserve(buyer, listing.id) for buyer in buyers])
    check_one_winner(engine, listing.id, replies)
    return replies


def check_one_winner(engine: Engine, listing_id: uuid.UUID, replies: Sequence[Reply]) -> None:
    """What the buyers were told, and what the database holds, both say exactly one got it."""
    statuses = Counter(reply.status for reply in replies)
    assert statuses == {201: 1, 409: len(replies) - 1}, statuses
    reasons = {reply.body["reason"] for reply in replies if reply.status == 409}
    assert reasons == {"reserved"}, reasons

    (won,) = [reply.body["id"] for reply in replies if reply.status == 201]
    with Session(engine) as session:
        assert session.get_one(Listing, listing_id).status == "RESERVED"
        rows = session.scalars(
            select(Reservation).where(Reservation.listing_id == listing_id)
        ).all()
        assert [(str(r.id), r.status) for r in rows] == [(won, "CREATED")]
        assert sum(r.status in LIVE_STATUSES for r in rows) == 1
        assert audit_counts(session, listing_id) == {"listing.reserved": 1}
        assert audit_counts(session, uuid.UUID(str(won))) == {"reservation.created": 1}


def audit_counts(session: Session, entity_id: uuid.UUID) -> dict[str, int]:
    return dict(
        session.execute(
            select(AuditLog.action, func.count())
            .where(AuditLog.entity_id == entity_id)
            .group_by(AuditLog.action)
        ).all()
    )
