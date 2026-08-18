from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.cache import redis
from app.routers import auth, links, redirect


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await redis.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Linkly",
        version="0.1.0",
        summary="A URL shortener with click analytics, Redis caching and rate limiting.",
        lifespan=lifespan,
    )

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(links.router)
    # Registered last: its "/{code}" route would otherwise swallow /auth, /api and /docs.
    app.include_router(redirect.router)
    return app


app = create_app()
