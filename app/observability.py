"""Request correlation and structured logging.

Every response carries an X-Request-ID and every log line for that request carries the
same value, so a user reporting "it failed at 14:02" turns into one grep instead of a
guess. Logs are JSON because that is what log aggregators can actually query.
"""

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

logger = logging.getLogger("linkly.access")

# Client-supplied ids are echoed into logs, so they are constrained before use.
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Health probes fire constantly and would drown everything else.
QUIET_PATHS = frozenset({"/healthz", "/readyz"})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        current = request_id.get()
        if current:
            payload["request_id"] = current
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Uvicorn ships its own handlers; drop them so everything goes out as JSON.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True


class RequestContextMiddleware:
    """Pure ASGI, not BaseHTTPMiddleware.

    BaseHTTPMiddleware wraps the response in a task group, which changes when background
    tasks run -- and this app records clicks in exactly such a task.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        current = incoming if SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id.set(current)

        started = time.perf_counter()
        status_code = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", current.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            path = scope.get("path", "")
            if path not in QUIET_PATHS:
                logger.info(
                    "%s %s %s",
                    scope.get("method", ""),
                    path,
                    status_code,
                    extra={
                        "fields": {
                            "method": scope.get("method"),
                            "path": path,
                            "status": status_code,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                        }
                    },
                )
            request_id.reset(token)
