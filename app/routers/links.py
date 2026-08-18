from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, desc, distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, qrcodes
from app.config import get_settings
from app.db import get_session
from app.deps import client_ip, get_current_user
from app.models import Click, Link, User
from app.qrcodes import MAX_BOX_SIZE, MEDIA_TYPES, MIN_BOX_SIZE, QrFormat
from app.ratelimit import enforce_limit
from app.schemas import DailyClicks, LinkCreate, LinkOut, LinkStats, ReferrerCount
from app.shortcode import RESERVED_CODES, generate_code

router = APIRouter(prefix="/api/links", tags=["links"])
settings = get_settings()

MAX_CODE_ATTEMPTS = 5


def _short_url(code: str) -> str:
    return f"{settings.base_url.rstrip('/')}/{code}"


def _to_out(link: Link) -> LinkOut:
    return LinkOut(
        code=link.code,
        target_url=link.target_url,
        short_url=_short_url(link.code),
        is_active=link.is_active,
        expires_at=link.expires_at,
        created_at=link.created_at,
    )


def _reject_self_reference(target_url: str) -> None:
    """Stop someone shortening a link that points back at us -- that is a redirect loop."""
    if urlparse(target_url).netloc == urlparse(settings.base_url).netloc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot shorten a link that points back at this service",
        )


@router.post("", response_model=LinkOut, status_code=status.HTTP_201_CREATED)
async def create_link(
    payload: LinkCreate,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LinkOut:
    await enforce_limit(f"create:{user.id}", settings.create_limit_per_hour)
    await enforce_limit(f"create:ip:{client_ip(request)}", settings.create_limit_per_hour * 3)
    _reject_self_reference(payload.target_url)

    if payload.custom_code:
        if payload.custom_code.lower() in RESERVED_CODES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="That code is reserved"
            )
        candidates = [payload.custom_code]
    else:
        candidates = [generate_code() for _ in range(MAX_CODE_ATTEMPTS)]

    # The unique index -- not a pre-flight SELECT -- is what actually prevents duplicates.
    # Two concurrent requests can both pass a check-then-insert; only one survives the index.
    for code in candidates:
        link = Link(
            code=code,
            target_url=payload.target_url,
            owner_id=user.id,
            expires_at=payload.expires_at,
        )
        session.add(link)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            continue
        await session.refresh(link)
        return _to_out(link)

    if payload.custom_code:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That code is taken")
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Could not allocate a unique code, please retry",
    )


@router.get("", response_model=list[LinkOut])
async def list_links(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[LinkOut]:
    rows = await session.scalars(
        select(Link)
        .where(Link.owner_id == user.id)
        .order_by(desc(Link.created_at))
        .limit(limit)
        .offset(offset)
    )
    return [_to_out(link) for link in rows]


async def _owned_link(code: str, user: User, session: AsyncSession) -> Link:
    link = await session.scalar(select(Link).where(Link.code == code, Link.owner_id == user.id))
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")
    return link


@router.get("/{code}", response_model=LinkOut)
async def get_link(
    code: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LinkOut:
    return _to_out(await _owned_link(code, user, session))


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(
    code: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    link = await _owned_link(code, user, session)
    await session.execute(delete(Link).where(Link.id == link.id))
    await session.commit()
    await cache.invalidate(code)


@router.get("/{code}/stats", response_model=LinkStats)
async def link_stats(
    code: str,
    days: int = Query(default=30, ge=1, le=365),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LinkStats:
    link = await _owned_link(code, user, session)

    totals = (
        await session.execute(
            select(func.count(Click.id), func.count(distinct(Click.ip_hash))).where(
                Click.link_id == link.id
            )
        )
    ).one()

    day = func.date_trunc("day", Click.clicked_at).label("day")
    daily_rows = (
        await session.execute(
            select(day, func.count(Click.id))
            .where(
                Click.link_id == link.id,
                Click.clicked_at >= func.now() - func.make_interval(0, 0, 0, days),
            )
            .group_by(day)
            .order_by(day)
        )
    ).all()

    # Group on the raw column and fill in "direct" afterwards. Coalescing here instead
    # would put a bind parameter in both the select list and the GROUP BY, and Postgres
    # treats two placeholders as two expressions -- it cannot see that they match.
    referrer_rows = (
        await session.execute(
            select(Click.referrer, func.count(Click.id))
            .where(Click.link_id == link.id)
            .group_by(Click.referrer)
            .order_by(desc(func.count(Click.id)))
            .limit(10)
        )
    ).all()

    return LinkStats(
        code=link.code,
        total_clicks=totals[0],
        unique_visitors=totals[1],
        daily=[DailyClicks(day=row[0].date(), count=row[1]) for row in daily_rows],
        top_referrers=[
            ReferrerCount(referrer=row[0] or "direct", count=row[1]) for row in referrer_rows
        ],
    )


@router.get(
    "/{code}/qr",
    response_class=Response,
    responses={200: {"content": {"image/png": {}, "image/svg+xml": {}}}},
)
async def link_qr(
    code: str,
    fmt: QrFormat = Query(default="png", alias="format"),
    box_size: int = Query(default=10, ge=MIN_BOX_SIZE, le=MAX_BOX_SIZE),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """A QR code for the short link, so it can go straight onto a slide or a poster."""
    link = await _owned_link(code, user, session)
    image = qrcodes.render(_short_url(link.code), fmt, box_size)

    return Response(
        content=image,
        media_type=MEDIA_TYPES[fmt],
        headers={
            # The code never changes once issued, so this image is safe to cache hard.
            "Cache-Control": "public, max-age=86400, immutable",
            "Content-Disposition": f'inline; filename="{link.code}.{fmt}"',
        },
    )
