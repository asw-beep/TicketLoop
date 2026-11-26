"""Sessions: one per request, committed when the request succeeds, rolled back otherwise.

A route asks for one by declaring a parameter of type DbSession:

    @router.post("/listings")
    def create_listing(body: ListingIn, db: DbSession) -> ListingOut: ...

Routes and services never call commit() themselves. The whole request is one transaction:
either every change it made is saved, or none is.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.redis.invalidation import attach


def create_session_factory(bind: Engine | Connection) -> sessionmaker[Session]:
    return sessionmaker(
        bind=bind,
        # Send SQL only when we say so (flush, commit, or a query we run), never as a side
        # effect of reading an attribute. Easier to reason about which statement runs when.
        autoflush=False,
        # Keep loaded values readable after commit. get_db commits before FastAPI serialises
        # the response, and an expired object would try to reload from a closed session.
        expire_on_commit=False,
    )


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    # Its commit bumps the cache versions of what it wrote (app/infrastructure/redis).
    attach(session, getattr(request.app.state, "cache", None))
    try:
        yield session  # the route runs here
        session.commit()
    except BaseException:
        # FastAPI re-raises the route's exception at the `yield`. A failed commit lands here
        # too. Either way nothing from this request is saved.
        session.rollback()
        raise
    finally:
        session.close()  # return the connection to the pool


# scope="function" ends get_db (commit included) BEFORE the response is sent. FastAPI's
# default for yield dependencies ends them after, so a commit that failed there would reach
# the client as a 200 for data that was never saved.
DbSession = Annotated[Session, Depends(get_db, scope="function")]
