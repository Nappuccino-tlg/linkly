"""A WebSocket per dashboard, a Redis subscription per link. See app/live.py.

Kept out of links.py because nothing here is a request/response endpoint: the auth is
different, the failure modes are close codes rather than status codes, and the lifetime is
minutes rather than milliseconds.
"""

import asyncio
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app import live
from app.db import SessionFactory
from app.models import Link, User
from app.security import decode_access_token

router = APIRouter(prefix="/api/links", tags=["live"])

#: How long a client gets to send its token after the socket opens. Long enough for a
#: slow phone, short enough that an unauthenticated socket cannot be parked open.
AUTH_TIMEOUT_SECONDS = 5.0

# Application close codes. 1008 (policy violation) would be correct for all of these and
# would tell the client nothing about which one happened; a dashboard needs to know
# whether to send the user to the sign-in page or just stop asking for this link.
UNAUTHORIZED = 4401
NOT_FOUND = 4404


async def _authenticate(websocket: WebSocket) -> User | None:
    """Read the bearer token from the first message, not from the URL.

    A browser cannot set an Authorization header on a WebSocket, so the usual shortcut is
    ?token=... -- which puts a live credential in a query string, and query strings end up
    in access logs, proxy logs, and the Referer header of anything the page opens next.
    A message costs one extra round trip and leaks nothing.
    """
    try:
        token = await asyncio.wait_for(websocket.receive_text(), AUTH_TIMEOUT_SECONDS)
    except (TimeoutError, WebSocketDisconnect):
        return None

    subject = decode_access_token(token)
    if subject is None:
        return None
    try:
        user_id = uuid.UUID(subject)
    except ValueError:
        return None

    # Its own short session rather than Depends(get_session): a dependency-provided
    # session would stay checked out of the pool for the whole life of the connection,
    # and these live for as long as someone leaves a tab open.
    async with SessionFactory() as session:
        return await session.get(User, user_id)


async def _owns(user: User, code: str) -> bool:
    async with SessionFactory() as session:
        link = await session.scalar(
            select(Link.id).where(Link.code == code, Link.owner_id == user.id)
        )
    return link is not None


async def _forward(websocket: WebSocket, messages) -> None:
    async for message in messages:
        await websocket.send_text(message)


async def _watch_for_disconnect(websocket: WebSocket) -> None:
    """A client that only listens still has to be listened to.

    Nothing above this reads from the socket once the token is in, and a disconnect
    arrives as a message like any other -- so without this the tab someone closed at
    lunchtime is not noticed until the next click on that link, which on a quiet link is
    never. Its subscription, and its queue, stay behind until then.
    """
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


@router.websocket("/{code}/live")
async def live_clicks(websocket: WebSocket, code: str) -> None:
    """Stream clicks on one link, as they happen, to its owner.

    Send the bearer token as the first message; after that the socket is one-way. Each
    message is `{"code": ..., "at": ..., "referrer": ...}` -- the referrer reduced to a
    host, or null.
    """
    await websocket.accept()

    user = await _authenticate(websocket)
    if user is None:
        await websocket.close(UNAUTHORIZED, "Could not validate credentials")
        return
    if not await _owns(user, code):
        # Deliberately the same answer whether the link is missing or belongs to somebody
        # else. Telling them apart turns this into a way to enumerate other people's codes.
        await websocket.close(NOT_FOUND, "Link not found")
        return

    async with live.hub.subscribe(live.topic(code)) as messages:
        tasks = [
            asyncio.create_task(_forward(websocket, messages)),
            asyncio.create_task(_watch_for_disconnect(websocket)),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
