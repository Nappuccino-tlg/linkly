"""Folding raw clicks into daily rollups, and dropping the raw rows once folded.

/stats used to aggregate the `clicks` table on every call. That is exact and it is fine
for a while, but the work grows with the traffic being reported on: a link with three
million clicks pays for three million rows to answer "how did last week go".

So closed days are folded into one row per link per day, and /stats reads those. What is
left to scan live is whatever has arrived since the newest folded day -- usually a few
hours of one link's traffic. The cost of a stats call stops tracking the link's success.

Two operations, deliberately separate:

    fold    recompute the rollups for recent closed days. Idempotent -- it replaces each
            day's row from the raw rows rather than adding to it, so running it twice,
            or after a straggler lands, converges instead of double counting.

    prune   delete raw rows past the retention window. Folds them first, in the same
            transaction, so it can never delete a day nothing has summarised.

Run `fold` often (hourly is plenty) and `prune` rarely (daily). Neither has to run at all
for /stats to be correct -- an unfolded day is simply still read from the raw table.
"""

import argparse
import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Date, cast, delete, distinct, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionFactory, engine
from app.models import REFERRER_KEY_MAX, Click, ClickDaily, ReferrerDaily
from app.observability import configure_logging

logger = logging.getLogger("linkly.rollup")

settings = get_settings()

DEFAULT_FOLD_DAYS = 7

# 'UTC' is written as a literal rather than bound as a parameter on purpose. This
# expression appears in a select list and in the matching GROUP BY, and Postgres compares
# two placeholders as two separate expressions even when they hold the same value.
UTC_ZONE = literal_column("'UTC'")


def utc_day(column):
    """The UTC calendar day a timestamptz falls on.

    Not date_trunc, which would follow whatever timezone the session happens to be set to
    and quietly re-bucket the same data differently on two machines.
    """
    return cast(func.timezone(UTC_ZONE, column), Date)


def today_utc() -> date:
    return datetime.now(UTC).date()


def day_start(day: date) -> datetime:
    """Midnight UTC at the start of `day`."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


async def fold_range(session: AsyncSession, start: date | None, end: date) -> tuple[int, int]:
    """Rebuild the rollups for [start, end) from raw clicks. `end` is exclusive.

    Returns the number of (link, day) and (link, day, referrer) rows written.
    """
    window = [Click.clicked_at < day_start(end)]
    if start is not None:
        window.append(Click.clicked_at >= day_start(start))

    day = utc_day(Click.clicked_at)

    daily_source = (
        select(
            Click.link_id.label("link_id"),
            day.label("day"),
            func.count(Click.id).label("clicks"),
            func.count(distinct(Click.ip_hash)).label("unique_visitors"),
        )
        .where(*window)
        .group_by(Click.link_id, day)
    )
    daily = insert(ClickDaily).from_select(
        ["link_id", "day", "clicks", "unique_visitors"], daily_source
    )
    # Replace, never accumulate: that is what makes a second run a no-op instead of a
    # doubling, and what lets a late-arriving click be picked up by the next fold.
    daily = daily.on_conflict_do_update(
        index_elements=["link_id", "day"],
        set_={"clicks": daily.excluded.clicks, "unique_visitors": daily.excluded.unique_visitors},
    )
    daily_result = await session.execute(daily)

    referrer_key = func.left(func.coalesce(Click.referrer, ""), REFERRER_KEY_MAX)
    referrer_source = (
        select(
            Click.link_id.label("link_id"),
            day.label("day"),
            referrer_key.label("referrer"),
            func.count(Click.id).label("clicks"),
        )
        .where(*window)
        # Grouped on the raw column; the coalesce and the truncation are applied in the
        # select list, where a bind parameter costs nothing.
        .group_by(Click.link_id, day, Click.referrer)
    )
    referrers = insert(ReferrerDaily).from_select(
        ["link_id", "day", "referrer", "clicks"], referrer_source
    )
    referrers = referrers.on_conflict_do_update(
        index_elements=["link_id", "day", "referrer"],
        set_={"clicks": referrers.excluded.clicks},
    )
    referrer_result = await session.execute(referrers)

    return daily_result.rowcount, referrer_result.rowcount


async def fold(session: AsyncSession, days: int = DEFAULT_FOLD_DAYS) -> tuple[int, int]:
    """Recompute the last `days` closed UTC days.

    Today is left alone: it is still accumulating, and /stats reads it live anyway.
    """
    end = today_utc()
    return await fold_range(session, end - timedelta(days=days), end)


async def prune(session: AsyncSession, keep_days: int) -> int:
    """Fold, then delete, every raw click older than `keep_days` whole days.

    The fold covers exactly the rows about to disappear and shares their transaction, so
    there is no window in which a day has been deleted but not summarised.
    """
    if keep_days < 1:
        raise ValueError("keep_days must be at least 1")

    cutoff = today_utc() - timedelta(days=keep_days)
    await fold_range(session, None, cutoff)
    result = await session.execute(delete(Click).where(Click.clicked_at < day_start(cutoff)))
    return result.rowcount


async def run(command: str, days: int = DEFAULT_FOLD_DAYS, keep_days: int = 0) -> None:
    """One rollup command, start to finish. The process lifecycle belongs to main()."""
    async with SessionFactory() as session:
        if command == "fold":
            daily_rows, referrer_rows = await fold(session, days)
            await session.commit()
            logger.info(
                "folded %s day rows and %s referrer rows",
                daily_rows,
                referrer_rows,
                extra={
                    "fields": {
                        "days": days,
                        "click_daily": daily_rows,
                        "referrer_daily": referrer_rows,
                    }
                },
            )
        else:
            deleted = await prune(session, keep_days)
            await session.commit()
            logger.info(
                "pruned %s raw click rows",
                deleted,
                extra={"fields": {"keep_days": keep_days, "deleted": deleted}},
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.rollup", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    fold_parser = sub.add_parser("fold", help="recompute rollups for recent closed days")
    fold_parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_FOLD_DAYS,
        help=f"how many closed days to recompute (default: {DEFAULT_FOLD_DAYS})",
    )

    prune_parser = sub.add_parser("prune", help="fold and then delete raw clicks past retention")
    prune_parser.add_argument(
        "--keep-days",
        type=int,
        default=settings.click_retention_days,
        help="days of raw clicks to keep (default: CLICK_RETENTION_DAYS)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    async def entrypoint() -> None:
        configure_logging()
        try:
            await run(
                args.command,
                getattr(args, "days", DEFAULT_FOLD_DAYS),
                getattr(args, "keep_days", 0),
            )
        finally:
            await engine.dispose()

    asyncio.run(entrypoint())


if __name__ == "__main__":  # pragma: no cover
    main()
