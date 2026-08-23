"""Measure what the rollups actually buy, against a real database.

Seeds one link with a lot of clicks spread over a lot of days, times /stats, folds, and
times it again. Both timings come from the real HTTP endpoint, not from a hand-written
copy of its query.

    python scripts/benchmark_stats.py --clicks 2000000 --days 365

Point DATABASE_URL and REDIS_URL at something disposable first: this writes millions of
rows and deletes the account it creates on the way out.
"""

import argparse
import asyncio
import os
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text

from app import rollup
from app.db import SessionFactory, engine
from app.main import app
from app.models import Click, ClickDaily, Link, ReferrerDaily, User

# Written straight to the table rather than through the redirect route: the point is to
# have a lot of rows, and generate_series produces them orders of magnitude faster than
# the API could. One statement, whatever the row count.
SEED = text("""
    INSERT INTO clicks (link_id, clicked_at, referrer, user_agent, ip_hash)
    SELECT
        :link_id,
        now() - (random() * :days) * interval '1 day',
        (ARRAY[
            'https://news.example/',
            'https://social.example/',
            'https://search.example/',
            NULL
        ])[1 + floor(random() * 4)],
        'benchmark',
        md5(floor(random() * :visitors)::text)
    FROM generate_series(1, :count)
""")


async def seed(link_id: uuid.UUID, clicks: int, days: int, visitors: int, batch: int) -> None:
    written = 0
    while written < clicks:
        chunk = min(batch, clicks - written)
        async with SessionFactory() as session:
            await session.execute(
                SEED, {"link_id": link_id, "days": days, "visitors": visitors, "count": chunk}
            )
            await session.commit()
        written += chunk
        print(f"  seeded {written:,} / {clicks:,}", end="\r", flush=True)
    print()


async def time_stats(client: AsyncClient, code: str, runs: int) -> tuple[float, dict]:
    """Median wall-clock milliseconds for a /stats call, plus the last response body."""
    timings = []
    body: dict = {}
    for _ in range(runs):
        started = time.perf_counter()
        response = await client.get(f"/api/links/{code}/stats")
        timings.append((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        body = response.json()
    return statistics.median(timings), body


async def table_size(name: str) -> str:
    async with SessionFactory() as session:
        return await session.scalar(
            text(f"SELECT pg_size_pretty(pg_total_relation_size('{name}'))")
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clicks", type=int, default=1_000_000)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--visitors", type=int, default=50_000)
    parser.add_argument("--runs", type=int, default=7, help="timed calls per measurement")
    parser.add_argument("--batch", type=int, default=250_000, help="rows per insert statement")
    parser.add_argument("--keep", action="store_true", help="leave the seeded data behind")
    args = parser.parse_args()

    email = f"benchmark-{uuid.uuid4().hex[:8]}@example.invalid"
    password = "benchmark-password"
    code = f"bench{uuid.uuid4().hex[:6]}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://benchmark") as anon:
        await anon.post("/auth/register", json={"email": email, "password": password})
        token = (
            await anon.post("/auth/token", data={"username": email, "password": password})
        ).json()["access_token"]

    async with AsyncClient(
        transport=transport,
        base_url="http://benchmark",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        created = await client.post(
            "/api/links", json={"target_url": "https://example.com/benchmark", "custom_code": code}
        )
        created.raise_for_status()

        async with SessionFactory() as session:
            link_id = await session.scalar(select(Link.id).where(Link.code == code))

        print(f"seeding {args.clicks:,} clicks over {args.days} days")
        await seed(link_id, args.clicks, args.days, args.visitors, args.batch)

        raw_ms, before = await time_stats(client, code, args.runs)

        print("folding")
        folded_started = time.perf_counter()
        async with SessionFactory() as session:
            daily_rows, referrer_rows = await rollup.fold_range(session, None, rollup.today_utc())
            await session.commit()
        fold_seconds = time.perf_counter() - folded_started

        rolled_ms, after = await time_stats(client, code, args.runs)

        print()
        print(f"clicks seeded          {args.clicks:,} over {args.days} days")
        print(f"rollup rows written    {daily_rows:,} daily, {referrer_rows:,} referrer")
        print(f"fold took              {fold_seconds:.1f}s")
        print(f"clicks table           {await table_size('clicks')}")
        print(f"click_daily table      {await table_size('click_daily')}")
        print(f"referrer_daily table   {await table_size('referrer_daily')}")
        print()
        print(f"/stats over raw rows   {raw_ms:.1f} ms")
        print(f"/stats over rollups    {rolled_ms:.1f} ms")
        if rolled_ms > 0:
            print(f"speedup                {raw_ms / rolled_ms:.1f}x")
        print()
        # The whole design rests on these agreeing. If they do not, the numbers above are
        # measuring a bug rather than an optimisation.
        matches = before == after
        print(f"answers identical      {'yes' if matches else 'NO -- FOLDING CHANGED THE ANSWER'}")
        if not matches:
            print(f"  before: {before}")
            print(f"  after:  {after}")

    if not args.keep:
        async with SessionFactory() as session:
            await session.execute(delete(ReferrerDaily).where(ReferrerDaily.link_id == link_id))
            await session.execute(delete(ClickDaily).where(ClickDaily.link_id == link_id))
            await session.execute(delete(Click).where(Click.link_id == link_id))
            await session.execute(delete(Link).where(Link.id == link_id))
            await session.execute(delete(User).where(User.email == email))
            await session.commit()
        print("cleaned up")

    await engine.dispose()


if __name__ == "__main__":
    print(f"started {datetime.now(UTC).isoformat(timespec='seconds')}")
    asyncio.run(main())
