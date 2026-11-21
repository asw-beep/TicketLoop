"""One error format for the whole API: RFC 9457 Problem Details.

Every error response, whatever caused it, has Content-Type `application/problem+json` and a
body like:

    {
      "type": "urn:ticketloop:problem:listing-not-available",
      "title": "Listing is not available",
      "status": 409,
      "detail": "Listing 5f0c... is RESERVED.",
      "instance": "/api/v1/listings/5f0c.../reservations",
      "request_id": "9b2e..."
    }

`type` is what client code should branch on; `title` is a short human summary that is the
same for every occurrence; `detail` explains this occurrence. `request_id` is our own
extension member: a user can quote it and we can find every log line for that request.

Business code raises AppError subclasses; it never builds HTTP responses itself.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.middleware import REQUEST_ID_HEADER

PROBLEM_JSON = "application/problem+json"
_TYPE_PREFIX = "urn:ticketloop:problem:"


class AppError(Exception):
    """An error our code raises on purpose. Subclasses set the status, code and title."""

    status: int = 400
    code: str = "bad-request"
    title: str = "Bad request"

    def __init__(self, detail: str | None = None, **extensions: Any) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.extensions = extensions  # extra fields for the body, e.g. current_status="RESERVED"


class NotFoundError(AppError):
    status = 404
    code = "not-found"
    title = "Resource not found"


class PermissionDeniedError(AppError):
    status = 403
    code = "permission-denied"
    title = "You are not allowed to do this"


class ConflictError(AppError):
    """The request is valid but clashes with current state, e.g. an illegal state transition."""

    status = 409
    code = "conflict"
    title = "Request conflicts with the current state"


def problem_response(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str | None = None,
    type_: str = "about:blank",
    headers: dict[str, str] | None = None,
    **extensions: Any,
) -> JSONResponse:
    body: dict[str, Any] = {"type": type_, "title": title, "status": status}
    if detail:
        body["detail"] = detail
    body["instance"] = request.url.path
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["request_id"] = request_id
    body.update(extensions)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    return problem_response(
        request,
        status=exc.status,
        title=exc.title,
        detail=exc.detail,
        type_=_TYPE_PREFIX + exc.code,
        **exc.extensions,
    )


async def _handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """404 for unknown paths, 405 for wrong methods, and any HTTPException raised directly."""
    try:
        title = HTTPStatus(exc.status_code).phrase
    except ValueError:
        title = "Error"
    detail = exc.detail if exc.detail != title else None
    # "about:blank" means: no extra meaning beyond the HTTP status code itself.
    return problem_response(
        request, status=exc.status_code, title=title, detail=detail, headers=exc.headers
    )


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default 422 body echoes the rejected input back. We leave it out on purpose:
    # on /auth/register that input contains the user's password.
    errors = [
        {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return problem_response(
        request,
        status=422,
        title="Request validation failed",
        detail="One or more fields are missing or invalid.",
        type_=_TYPE_PREFIX + "validation-error",
        errors=errors,
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Last resort for bugs. The client learns nothing about our internals.

    Starlette runs this handler outside our request middleware, so the middleware never sees
    this response; we add the request ID header here ourselves. The traceback is logged by
    the server (uvicorn) with the request ID attached, because the ID is still bound in the
    logging context.
    """
    response = problem_response(
        request,
        status=500,
        title="Internal Server Error",
        detail="An unexpected error occurred. Quote the request_id if you report it.",
    )
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
