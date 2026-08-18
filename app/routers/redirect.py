import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache
from app.db import SessionFactory, get_session
from app.deps import client_ip
from app.models import Click, Link
from app.security import hash_ip

router = APIRouter(tags=["redirect"])

REFERRER_MAX_LEN = 2048
USER_AGENT_MAX_LEN = 1024

NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")
GONE = HTTPException(status_code=status.HTTP_410_GONE, detail="Link has expired")


async def record_click(
    link_id: uuid.UUID, referrer: str | None, user_agent: str | None, ip_hash: str | None
) -> None:
    """Runs after the response is sent, so analytics never slow down a redirect.

    Opens its own session: the request-scoped one is already closed by this point.
    """
    async with SessionFactory() as session:
        session.add(
            Click(
                link_id=link_id,
                referrer=referrer[:REFERRER_MAX_LEN] if referrer else None,
                user_agent=user_agent[:USER_AGENT_MAX_LEN] if user_agent else None,
                ip_hash=ip_hash,
            )
        )
        await session.commit()


async def _resolve(code: str, session: AsyncSession) -> cache.CachedLink:
    """Cache first; fall back to Postgres and warm the cache on a miss.

    Only resolvable links are ever cached, so a hit needs no further checks beyond expiry.
    """
    hit = await cache.get_link(code)
    if hit is not None:
        return hit

    link = await session.scalar(select(Link).where(Link.code == code))
    if link is None or not link.is_active:
        raise NOT_FOUND

    resolved = cache.CachedLink(id=link.id, target_url=link.target_url, expires_at=link.expires_at)
    await cache.put_link(code, resolved)
    return resolved


@router.get("/{code}", include_in_schema=False)
async def follow(
    code: str,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    resolved = await _resolve(code, session)

    if resolved.expires_at is not None and resolved.expires_at <= datetime.now(UTC):
        await cache.invalidate(code)
        raise GONE

    background.add_task(
        record_click,
        resolved.id,
        request.headers.get("referer"),
        request.headers.get("user-agent"),
        hash_ip(client_ip(request)),
    )

    # 307 keeps the method and, unlike 301, is not cached by the browser -- otherwise the
    # first click would be the only one we ever count.
    return RedirectResponse(resolved.target_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
