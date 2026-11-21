"""GET /api/v1/health: is the API up, and can it reach Postgres?

Returns 200 when every check passes and 503 when any fails, so Docker, a load balancer or
an uptime monitor can act on the status code alone. The body says which check failed.
A failed check is a status report, not an API error, so it does not use problem+json.
"""

from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter(tags=["health"])
log = structlog.get_logger(__name__)

CheckStatus = Literal["ok", "unavailable"]


class HealthChecks(BaseModel):
    database: CheckStatus


class HealthResponse(BaseModel):
    status: CheckStatus
    checks: HealthChecks


def database_is_reachable(request: Request) -> bool:
    """Run SELECT 1. A dependency, so tests can swap it out with app.dependency_overrides."""
    engine = request.app.state.engine
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # Log the driver's message only: it names host and port, never the password.
        reason = str(getattr(exc, "orig", None) or exc).splitlines()[0]
        log.warning("health.database_unreachable", error_type=type(exc).__name__, reason=reason)
        return False
    return True


# A plain `def`, not `async def`: SQLAlchemy here is synchronous and blocks while it waits for
# Postgres. FastAPI runs `def` endpoints in a thread pool so the blocking call does not
# freeze the event loop that serves every other request.
@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "A dependency is unavailable"}},
)
def health(
    response: Response,
    database_ok: Annotated[bool, Depends(database_is_reachable)],
) -> HealthResponse:
    if database_ok:
        return HealthResponse(status="ok", checks=HealthChecks(database="ok"))
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="unavailable", checks=HealthChecks(database="unavailable"))
