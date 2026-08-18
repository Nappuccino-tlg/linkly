"""One error shape for the whole API.

FastAPI's default is {"detail": ...} for HTTP errors and a differently shaped list for
validation errors. Clients then need two parsers for the same failure. Everything here
answers with {"error": {...}} and carries the request id, so a support conversation can
start from the id instead of a screenshot.
"""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability import request_id


def _body(status_code: int, message: str, details: object | None = None) -> dict[str, object]:
    error: dict[str, object] = {"status": status_code, "message": message}
    if details is not None:
        error["details"] = details
    current = request_id.get()
    if current:
        error["request_id"] = current
    return {"error": error}


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_body(exc.status_code, str(exc.detail)),
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = [
        {"field": ".".join(str(part) for part in err["loc"][1:]), "message": err["msg"]}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_body(status.HTTP_422_UNPROCESSABLE_ENTITY, "Request validation failed", details),
    )


def register(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
