from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from sqlalchemy import text

from app import errors
from app.cache import redis
from app.db import SessionFactory
from app.observability import RequestContextMiddleware, configure_logging
from app.routers import auth, links, redirect


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    yield
    await redis.aclose()


async def _dependency_status() -> dict[str, str]:
    """Actually talk to Postgres and Redis rather than assuming they are there."""
    # A probe reports failures, it never raises -- an unreachable Redis must still
    # produce a readable 503 rather than a 500 with a stack trace.
    checks = {}
    try:
        async with SessionFactory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {type(exc).__name__}"
    return checks


def create_app() -> FastAPI:
    app = FastAPI(
        title="Linkly",
        version="0.2.0",
        summary="A URL shortener with click analytics, Redis caching and rate limiting.",
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    errors.register(app)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        """Liveness: the process is up. Never touches a dependency, so it never flaps."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    async def readyz(response: Response) -> dict[str, object]:
        """Readiness: this instance can actually serve traffic right now."""
        checks = await _dependency_status()
        healthy = all(value == "ok" for value in checks.values())
        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ok" if healthy else "degraded", "checks": checks}

    app.include_router(auth.router)
    app.include_router(links.router)
    # Registered last: its "/{code}" route would otherwise swallow /auth, /api and /docs.
    app.include_router(redirect.router)
    return app


app = create_app()
