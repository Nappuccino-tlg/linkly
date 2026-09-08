"""The live click feed.

A click recorded here has to reach a dashboard connected somewhere else, which is what
app/live.py goes through Redis for. These run a single process, so what they can prove is
that the publish and the subscribe are wired to the same Hub and that the round trip
through Redis carries the payload intact -- hubcast's own suite is where two Hubs on one
Redis are checked against each other.
"""

import asyncio

import pytest
from sqlalchemy import delete

from app import live
from app.db import SessionFactory
from app.models import User
from app.routers import live as live_router
from app.security import create_access_token
from tests.api.conftest import WebSocketSession


async def make_link(auth_client, target="https://example.com/product") -> str:
    response = await auth_client.post("/api/links", json={"target_url": target})
    assert response.status_code == 201
    return response.json()["code"]


def bearer(auth_client) -> str:
    return auth_client.headers["Authorization"].removeprefix("Bearer ")


async def token_for(client, email: str, password: str = "supersecret123") -> str:
    await client.post("/auth/register", json={"email": email, "password": password})
    response = await client.post("/auth/token", data={"username": email, "password": password})
    return response.json()["access_token"]


async def watching(code: str, token: str) -> WebSocketSession:
    """An authenticated session on a link's feed, subscribed and ready to receive.

    Nothing is sent to say the subscription is live, and a click published before it lands
    is published to nobody -- so this waits rather than races.
    """
    session = WebSocketSession(f"/api/links/{code}/live")
    await session.__aenter__()
    await session.send_text(token)
    await asyncio.sleep(0.2)
    return session


# -- the point of it -----------------------------------------------------------------


async def test_a_click_reaches_a_dashboard_watching_the_link(client, auth_client):
    code = await make_link(auth_client)

    feed = await watching(code, bearer(auth_client))
    try:
        await client.get(f"/{code}")

        event = await feed.receive_json()
        assert event["code"] == code
        assert event["at"]
        assert event["referrer"] is None
    finally:
        await feed.__aexit__(None, None, None)


async def test_two_dashboards_on_one_link_both_receive_it(client, auth_client):
    """One Redis subscription, two watchers: the fan-out happens in the process."""
    code = await make_link(auth_client)

    first = await watching(code, bearer(auth_client))
    second = await watching(code, bearer(auth_client))
    try:
        await client.get(f"/{code}")

        assert (await first.receive_json())["code"] == code
        assert (await second.receive_json())["code"] == code
    finally:
        await first.__aexit__(None, None, None)
        await second.__aexit__(None, None, None)


async def test_a_click_on_another_link_is_not_delivered(client, auth_client):
    watched = await make_link(auth_client)
    other = await make_link(auth_client, target="https://example.com/other")

    feed = await watching(watched, bearer(auth_client))
    try:
        await client.get(f"/{other}")
        await client.get(f"/{watched}")

        # If topics leaked into each other, the first event through would be the one for
        # `other` -- which is why both are clicked rather than only the one that must not
        # arrive.
        assert (await feed.receive_json())["code"] == watched
    finally:
        await feed.__aexit__(None, None, None)


async def test_the_feed_reports_the_referrer_host_and_nothing_else(client, auth_client):
    """The path and query of a Referer are somebody else's business -- a search term, a
    session id, an unlisted page -- and none of that belongs on a dashboard."""
    code = await make_link(auth_client)

    feed = await watching(code, bearer(auth_client))
    try:
        await client.get(
            f"/{code}",
            headers={"referer": "https://news.example.org/search?q=private+thing"},
        )

        assert (await feed.receive_json())["referrer"] == "news.example.org"
    finally:
        await feed.__aexit__(None, None, None)


# -- who is allowed to watch ---------------------------------------------------------


async def closed_with(session: WebSocketSession) -> int:
    message = await session.receive()
    assert message["type"] == "websocket.close", message
    return message["code"]


async def test_a_bad_token_is_refused(auth_client):
    code = await make_link(auth_client)

    async with WebSocketSession(f"/api/links/{code}/live") as session:
        await session.send_text("not-a-token")
        assert await closed_with(session) == live_router.UNAUTHORIZED


async def test_a_token_for_a_deleted_user_is_refused(client, auth_client):
    """A signed token outlives the row it names. Decoding one is not the same as the user
    still existing, and only a lookup tells the difference."""
    code = await make_link(auth_client)
    token = await token_for(client, "gone@example.com")

    async with SessionFactory() as db:
        await db.execute(delete(User).where(User.email == "gone@example.com"))
        await db.commit()

    async with WebSocketSession(f"/api/links/{code}/live") as session:
        await session.send_text(token)
        assert await closed_with(session) == live_router.UNAUTHORIZED


async def test_a_token_that_names_something_other_than_a_user_is_refused(auth_client):
    """Our own tokens carry a user id, but a valid signature only proves the token came
    from us -- not that whatever is inside it is still shaped like one."""
    code = await make_link(auth_client)
    token = create_access_token("not-a-uuid")

    async with WebSocketSession(f"/api/links/{code}/live") as session:
        await session.send_text(token)
        assert await closed_with(session) == live_router.UNAUTHORIZED


async def test_a_socket_that_never_authenticates_is_closed(auth_client, monkeypatch):
    code = await make_link(auth_client)
    monkeypatch.setattr(live_router, "AUTH_TIMEOUT_SECONDS", 0.05)

    async with WebSocketSession(f"/api/links/{code}/live") as session:
        assert await closed_with(session) == live_router.UNAUTHORIZED


async def test_a_socket_that_leaves_before_authenticating_is_not_an_error(auth_client):
    code = await make_link(auth_client)

    session = WebSocketSession(f"/api/links/{code}/live")
    await session.__aenter__()
    await session.__aexit__(None, None, None)


async def test_someone_elses_link_cannot_be_watched(client, auth_client):
    code = await make_link(auth_client)
    intruder = await token_for(client, "intruder@example.com")

    async with WebSocketSession(f"/api/links/{code}/live") as session:
        await session.send_text(intruder)
        assert await closed_with(session) == live_router.NOT_FOUND


async def test_a_link_that_does_not_exist_cannot_be_watched(auth_client):
    """The same answer as somebody else's link, on purpose: telling them apart turns this
    into a way to enumerate other people's codes."""
    async with WebSocketSession("/api/links/nosuchcode/live") as session:
        await session.send_text(bearer(auth_client))
        assert await closed_with(session) == live_router.NOT_FOUND


# -- when the feed cannot be delivered ------------------------------------------------


async def test_a_redis_that_will_not_publish_does_not_break_the_redirect(
    client, auth_client, monkeypatch, caplog
):
    """The click is committed before the publish, so losing the live update is cosmetic
    and must not turn a working redirect into a 500."""
    code = await make_link(auth_client)

    async def explode(*args, **kwargs):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(live.hub, "publish", explode)

    response = await client.get(f"/{code}")
    assert response.status_code == 307

    stats = await auth_client.get(f"/api/links/{code}/stats")
    assert stats.json()["total_clicks"] == 1
    assert "live click feed unavailable" in caplog.text


@pytest.mark.parametrize(
    ("referrer", "expected"),
    [
        (None, None),
        ("", None),
        ("https://example.org/page", "example.org"),
        ("https://example.org:8443/page", "example.org:8443"),
        ("not a url at all", None),
    ],
)
def test_a_referrer_is_reduced_to_a_host(referrer, expected):
    assert live._referrer_host(referrer) == expected
