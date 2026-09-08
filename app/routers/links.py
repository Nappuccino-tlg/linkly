from datetime import timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, desc, distinct, func, select, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, qrcodes, ratelimit
from app.config import get_settings
from app.db import get_session
from app.deps import client_ip, get_current_user
from app.models import REFERRER_KEY_MAX, Click, ClickDaily, Link, ReferrerDaily, User
from app.qrcodes import MAX_BOX_SIZE, MEDIA_TYPES, MIN_BOX_SIZE, QrFormat
from app.rollup import day_start, today_utc, utc_day
from app.schemas import (
    DailyClicks,
    LinkCreate,
    LinkOut,
    LinkPage,
    LinkStats,
    LinkUpdate,
    ReferrerCount,
)
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
    await ratelimit.enforce(ratelimit.link_creation, f"user:{user.id}")
    await ratelimit.enforce(ratelimit.link_creation_by_address, f"ip:{client_ip(request)}")
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


@router.get("", response_model=LinkPage)
async def list_links(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LinkPage:
    # The total comes back with the page: without it a client cannot render "page 3 of 7"
    # or know whether to show a next button, and would have to guess by over-fetching.
    total = await session.scalar(select(func.count(Link.id)).where(Link.owner_id == user.id))
    rows = await session.scalars(
        select(Link)
        .where(Link.owner_id == user.id)
        .order_by(desc(Link.created_at))
        .limit(limit)
        .offset(offset)
    )
    return LinkPage(
        items=[_to_out(link) for link in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


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


@router.patch("/{code}", response_model=LinkOut)
async def update_link(
    code: str,
    payload: LinkUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LinkOut:
    """Repoint, expire or disable a link without changing the code people already have."""
    link = await _owned_link(code, user, session)

    changes = payload.model_dump(exclude_unset=True)
    if "target_url" in changes:
        _reject_self_reference(changes["target_url"])
        link.target_url = changes["target_url"]
    if "is_active" in changes:
        link.is_active = changes["is_active"]
    if "expires_at" in changes:
        link.expires_at = changes["expires_at"]

    await session.commit()
    await session.refresh(link)

    # Every one of those fields decides where the code resolves, and the cached copy is
    # now wrong. Dropping it is what makes the change take effect immediately.
    await cache.invalidate(code)
    return _to_out(link)


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
    """Traffic for one link, read from the daily rollups plus whatever has not been folded.

    See app/rollup.py for the folding itself. Nothing here depends on the fold having run:
    the boundary between summarised and live comes from the newest rollup row, so a day
    nobody has folded yet is simply still counted from the raw table.
    """
    link = await _owned_link(code, user, session)

    watermark = await session.scalar(
        select(func.max(ClickDaily.day)).where(ClickDaily.link_id == link.id)
    )
    live = [Click.link_id == link.id]
    if watermark is not None:
        live.append(Click.clicked_at >= day_start(watermark + timedelta(days=1)))

    # Live rows are bucketed by day before anything is added up, because that is how the
    # rollups store them. Counting distinct visitors straight across the live range instead
    # would answer a different question the moment that range covers more than one day --
    # and the answer would then change the first time a fold ran. Same shape, same number.
    day = utc_day(Click.clicked_at)
    live_by_day = (
        select(
            day.label("day"),
            func.count(Click.id).label("clicks"),
            func.count(distinct(Click.ip_hash)).label("unique_visitors"),
        )
        .where(*live)
        .group_by(day)
        .subquery()
    )

    rolled_totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(ClickDaily.clicks), 0),
                func.coalesce(func.sum(ClickDaily.unique_visitors), 0),
            ).where(ClickDaily.link_id == link.id)
        )
    ).one()
    live_totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(live_by_day.c.clicks), 0),
                func.coalesce(func.sum(live_by_day.c.unique_visitors), 0),
            )
        )
    ).one()

    first_day = today_utc() - timedelta(days=days - 1)

    # Rollup rows and live rows cover disjoint days by construction, so the union needs no
    # deduplication -- the outer grouping just puts both shapes into one result.
    rolled_daily = select(
        ClickDaily.day.label("day"),
        ClickDaily.clicks.label("clicks"),
        ClickDaily.unique_visitors.label("unique_visitors"),
    ).where(ClickDaily.link_id == link.id, ClickDaily.day >= first_day)
    live_daily = select(
        live_by_day.c.day, live_by_day.c.clicks, live_by_day.c.unique_visitors
    ).where(live_by_day.c.day >= first_day)
    buckets = union_all(rolled_daily, live_daily).subquery()
    daily_rows = (
        await session.execute(
            select(
                buckets.c.day,
                func.sum(buckets.c.clicks),
                func.sum(buckets.c.unique_visitors),
            )
            .group_by(buckets.c.day)
            .order_by(buckets.c.day)
        )
    ).all()

    # Group on the raw column and fill in "direct" afterwards. Coalescing in the GROUP BY
    # instead would put a bind parameter in both the select list and the grouping, and
    # Postgres treats two placeholders as two expressions -- it cannot see that they match.
    rolled_referrers = select(
        ReferrerDaily.referrer.label("referrer"), ReferrerDaily.clicks.label("clicks")
    ).where(ReferrerDaily.link_id == link.id)
    live_referrers = (
        select(
            func.left(func.coalesce(Click.referrer, ""), REFERRER_KEY_MAX).label("referrer"),
            func.count(Click.id).label("clicks"),
        )
        .where(*live)
        .group_by(Click.referrer)
    )
    sources = union_all(rolled_referrers, live_referrers).subquery()
    referrer_total = func.sum(sources.c.clicks).label("total_clicks")
    referrer_rows = (
        await session.execute(
            select(sources.c.referrer, referrer_total)
            .group_by(sources.c.referrer)
            # Name breaks the tie, so equal counts do not come back in whatever order the
            # planner felt like today.
            .order_by(desc(referrer_total), sources.c.referrer)
            .limit(10)
        )
    ).all()

    return LinkStats(
        code=link.code,
        total_clicks=rolled_totals[0] + live_totals[0],
        unique_visitors=rolled_totals[1] + live_totals[1],
        daily=[DailyClicks(day=row[0], count=row[1], unique_visitors=row[2]) for row in daily_rows],
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
