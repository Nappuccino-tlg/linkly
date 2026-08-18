"""One error shape for the whole API.

FastAPI's default is {"detail": ...} for HTTP errors and a differently shaped list for
validation errors. Clients then need two parsers for the same failure. Everything here
answers with {"error": {...}} and carries the request id, so a support conversation can
start from the id instead of a screenshot.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability import SCOPE_KEY, request_id

logger = logging.getLogger("linkly.error")


def _current_id(request: Request) -> str | None:
    """Scope first: Starlette's own 500 handler runs after the middleware reset the contextvar."""
    return request.scope.get(SCOPE_KEY) or request_id.get()


def _response(
    request: Request,
    status_code: int,
    message: str,
    details: object | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, object] = {"status": status_code, "message": message}
    if details is not None:
        error["details"] = details

    current = _current_id(request)
    if current:
        error["request_id"] = current

    # Set explicitly rather than relying on the middleware: on the 500 path the response is
    # produced above it, so the header would otherwise be missing from exactly the replies
    # a user needs to quote back.
    merged = dict(headers or {})
    if current:
        merged["X-Request-ID"] = current

    return JSONResponse(status_code=status_code, content={"error": error}, headers=merged)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _response(
        request, exc.status_code, str(exc.detail), headers=getattr(exc, "headers", None)
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = [
        {"field": ".".join(str(part) for part in err["loc"][1:]), "message": err["msg"]}
        for err in exc.errors()
    ]
    return _response(
        request, status.HTTP_422_UNPROCESSABLE_ENTITY, "Request validation failed", details
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort.

    An unreachable database would otherwise return a bare plain-text 500 with no request
    id -- exactly the moment a user has nothing useful to report. The traceback goes to
    the log, correlated by id; the client gets the id and nothing else.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return _response(request, status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error")


def register(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
