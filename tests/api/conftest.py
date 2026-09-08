"""Fixtures backed by a real Postgres and a real Redis.

Unique indexes, SQL aggregation and TTL behaviour are most of what these tests check,
and none of that survives a mock.
"""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app import live
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
        await conn.execute(
            text(
                "TRUNCATE users, links, clicks, click_daily, referrer_daily "
                "RESTART IDENTITY CASCADE"
            )
        )
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


@pytest.fixture(scope="package", autouse=True)
async def _live_hub():
    """Start the Hub the way app.main's lifespan does.

    ASGITransport does not run lifespan events, so without this the live endpoint would
    subscribe to a Hub whose listener was never started and then quietly receive nothing
    -- which is also exactly what production looks like if lifespan is ever skipped, so
    it is worth knowing the tests would notice.
    """
    await live.hub.start()
    yield
    await live.hub.stop()


class WebSocketSession:
    """Drives a WebSocket endpoint by speaking ASGI to it directly.

    Starlette's TestClient is the obvious tool and cannot be used here: it runs the app in
    an event loop of its own on another thread, while this suite's asyncpg pool and Hub
    are both bound to the loop that created them. httpx has no WebSocket support at all.

    So this exchanges the handful of ASGI messages the endpoint actually uses. It is a
    little more code than a client library, and in exchange what is under test is the
    endpoint rather than a library's idea of one.
    """

    def __init__(self, path: str, *, timeout: float = 3.0):
        self.path = path
        self.timeout = timeout
        self._to_server: asyncio.Queue = asyncio.Queue()
        self._from_server: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "WebSocketSession":
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": self.path,
            "raw_path": self.path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 45678),
            "server": ("testserver", 80),
            "subprotocols": [],
        }
        self._task = asyncio.create_task(app(scope, self._to_server.get, self._from_server.put))
        await self._to_server.put({"type": "websocket.connect"})
        opened = await self.receive()
        assert opened["type"] == "websocket.accept", opened
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._to_server.put({"type": "websocket.disconnect", "code": 1000})
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    async def send_text(self, text: str) -> None:
        await self._to_server.put({"type": "websocket.receive", "text": text})

    async def receive(self) -> dict:
        """The next message, or fail -- never hang the suite waiting for one.

        Waits on the endpoint's task alongside the queue so that an endpoint which raised,
        or which returned without sending anything, reports that instead of timing out
        with nothing to say.
        """
        getter = asyncio.ensure_future(self._from_server.get())
        done, _ = await asyncio.wait(
            {getter, self._task}, timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if getter in done:
            return getter.result()

        getter.cancel()
        if self._task in done:
            self._task.result()  # re-raises whatever the endpoint raised
            raise AssertionError("the endpoint finished without sending a message")
        raise TimeoutError(f"no message from {self.path} within {self.timeout}s")

    async def receive_json(self) -> dict:
        message = await self.receive()
        assert message["type"] == "websocket.send", message
        return json.loads(message["text"])
