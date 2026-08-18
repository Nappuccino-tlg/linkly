"""Fixtures backed by a real Postgres and a real Redis.

Unique indexes, SQL aggregation and TTL behaviour are most of what these tests check,
and none of that survives a mock.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.cache import redis
from app.db import engine
from app.main import app
from app.models import Base


@pytest.fixture(scope="package", autouse=True)
async def _schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_state():
    """Each test starts with empty tables, an empty cache and reset rate-limit counters."""
    yield
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users, links, clicks RESTART IDENTITY CASCADE"))
    await redis.flushdb()


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
async def auth_client(client):
    """A second client, registered and carrying a bearer token.

    Deliberately not the `client` fixture with a header bolted on: tests that assert an
    endpoint is owner-only need an anonymous client alongside this one, and mutating the
    shared instance would silently authenticate it too.
    """
    email = "owner@example.com"
    password = "supersecret123"
    await client.post("/auth/register", json={"email": email, "password": password})
    response = await client.post("/auth/token", data={"username": email, "password": password})
    token = response.json()["access_token"]

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac


@pytest.fixture
async def tolerant_client():
    """A client that lets server errors come back as 500s instead of re-raising them.

    The default transport re-raises, which is right for most tests but makes the 500
    handler itself untestable.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as ac:
        yield ac
