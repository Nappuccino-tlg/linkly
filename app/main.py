from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import errors
from app.cache import redis
from app.db import SessionFactory
from app.observability import RequestContextMiddleware, configure_logging
from app.routers import auth, links, redirect

STATIC_DIR = Path(__file__).parent / "static"


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

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/app/")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        """Served explicitly.

        Browsers request /favicon.ico unprompted, and without this route it falls through
        to "/{code}" and costs a database lookup on every single page view.
        """
        return FileResponse(
            STATIC_DIR / "favicon.svg",
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    app.include_router(auth.router)
    app.include_router(links.router)

    # The dashboard is plain HTML, CSS and one script -- no build step, no bundler, and
    # nothing to install. It talks to the same public API as any other client would.
    app.mount("/app", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")

    # Registered last: its "/{code}" route would otherwise swallow /auth, /api and /docs.
    app.include_router(redirect.router)
    return app


app = create_app()
