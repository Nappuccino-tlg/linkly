"""The rollups, and the promise that folding never changes an answer.

Several of these run the same query twice -- once against raw clicks, once after those
rows have been folded and in some cases deleted -- and assert the two agree. That is the
whole contract: /stats must not be able to tell whether the fold has run.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import rollup
from app.db import SessionFactory
from app.models import Click, ClickDaily, Link, ReferrerDaily

TODAY = datetime.now(UTC).date()


async def _make_link(auth_client, code="rolled", target="https://example.com/target"):
    response = await auth_client.post(
        "/api/links", json={"target_url": target, "custom_code": code}
    )
    assert response.status_code == 201
    return response.json()


async def _link_id(code: str):
    async with SessionFactory() as session:
        return await session.scalar(select(Link.id).where(Link.code == code))


async def _add_clicks(link_id, days_ago: int, *, count: int = 1, ip="ip-a", referrer=None):
    """Raw click rows at a controlled time, standing in for traffic on an earlier day.

    Midday, so that shifting the timestamp by whole days cannot land on a boundary.
    """
    moment = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(
        days=days_ago
    )
    async with SessionFactory() as session:
        for _ in range(count):
            session.add(Click(link_id=link_id, clicked_at=moment, ip_hash=ip, referrer=referrer))
        await session.commit()


async def _fold(days: int = 7):
    async with SessionFactory() as session:
        result = await rollup.fold(session, days)
        await session.commit()
        return result


async def _prune(keep_days: int):
    async with SessionFactory() as session:
        deleted = await rollup.prune(session, keep_days)
        await session.commit()
        return deleted


async def _rollup_rows(link_id):
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(ClickDaily.day, ClickDaily.clicks, ClickDaily.unique_visitors)
                .where(ClickDaily.link_id == link_id)
                .order_by(ClickDaily.day)
            )
        ).all()
    return [(row[0], row[1], row[2]) for row in rows]


async def _raw_count(link_id):
    async with SessionFactory() as session:
        return await session.scalar(select(func.count(Click.id)).where(Click.link_id == link_id))


async def test_fold_summarises_a_closed_day(auth_client):
    await _make_link(auth_client)
    link_id = await _link_id("rolled")
    await _add_clicks(link_id, days_ago=1, count=3, ip="visitor-1")
    await _add_clicks(link_id, days_ago=1, count=2, ip="visitor-2")

    await _fold()

    assert await _rollup_rows(link_id) == [(TODAY - timedelta(days=1), 5, 2)]


async def test_fold_leaves_today_alone(auth_client, client):
    """Today is still accumulating, and /stats reads it live anyway."""
    await _make_link(auth_client, code="fresh")
    await client.get("/fresh", follow_redirects=False)

    await _fold()

    assert await _rollup_rows(await _link_id("fresh")) == []


async def test_folding_twice_does_not_double_a_day(auth_client):
    await _make_link(auth_client)
    link_id = await _link_id("rolled")
    await _add_clicks(link_id, days_ago=2, count=4)

    await _fold()
    await _fold()

    assert await _rollup_rows(link_id) == [(TODAY - timedelta(days=2), 4, 1)]


async def test_a_click_that_lands_after_its_day_was_folded_is_picked_up(auth_client):
    """A background task can cross midnight. The next fold recomputes, so it converges."""
    await _make_link(auth_client)
    link_id = await _link_id("rolled")
    await _add_clicks(link_id, days_ago=1, count=2)
    await _fold()

    await _add_clicks(link_id, days_ago=1, count=1)
    await _fold()

    assert await _rollup_rows(link_id) == [(TODAY - timedelta(days=1), 3, 1)]


async def test_stats_are_identical_before_and_after_folding(auth_client, client):
    """The point of the whole exercise: folding is invisible from the outside."""
    await _make_link(auth_client, code="same")
    link_id = await _link_id("same")
    await _add_clicks(link_id, days_ago=1, count=3, ip="a", referrer="https://news.example/")
    await _add_clicks(link_id, days_ago=1, count=1, ip="b")
    await _add_clicks(link_id, days_ago=3, count=2, ip="a", referrer="https://news.example/")
    await client.get("/same", follow_redirects=False)

    before = (await auth_client.get("/api/links/same/stats")).json()
    await _fold()
    after = (await auth_client.get("/api/links/same/stats")).json()

    assert before == after
    assert after["total_clicks"] == 7
    assert len(after["daily"]) == 3


async def test_stats_survive_the_raw_rows_being_deleted(auth_client, client):
    """Retention removes the rows the numbers were computed from. The numbers stay."""
    await _make_link(auth_client, code="kept")
    link_id = await _link_id("kept")
    await _add_clicks(link_id, days_ago=40, count=5, ip="a", referrer="https://news.example/")
    await _add_clicks(link_id, days_ago=40, count=2, ip="b")
    await client.get("/kept", follow_redirects=False)

    before = (await auth_client.get("/api/links/kept/stats")).json()
    deleted = await _prune(keep_days=30)
    after = (await auth_client.get("/api/links/kept/stats")).json()

    assert deleted == 7
    assert await _raw_count(link_id) == 1  # the click from today, well inside retention
    assert after["total_clicks"] == before["total_clicks"] == 8
    assert after["unique_visitors"] == before["unique_visitors"]
    assert {row["referrer"] for row in after["top_referrers"]} == {
        "https://news.example/",
        "direct",
    }


async def test_prune_folds_days_that_nobody_folded_first(auth_client):
    """Deleting a day no rollup covers would lose it outright, so prune folds it itself."""
    await _make_link(auth_client, code="unfolded")
    link_id = await _link_id("unfolded")
    await _add_clicks(link_id, days_ago=100, count=4)

    await _prune(keep_days=30)

    assert await _raw_count(link_id) == 0
    assert await _rollup_rows(link_id) == [(TODAY - timedelta(days=100), 4, 1)]


async def test_referrers_are_merged_across_rollups_and_live_rows(auth_client):
    await _make_link(auth_client, code="refs")
    link_id = await _link_id("refs")
    await _add_clicks(link_id, days_ago=2, count=3, referrer="https://a.example/")
    await _add_clicks(link_id, days_ago=1, count=1, referrer="https://a.example/")
    await _add_clicks(link_id, days_ago=1, count=2, referrer="https://b.example/")
    await _fold()
    await _add_clicks(link_id, days_ago=0, count=1, referrer="https://b.example/")

    stats = (await auth_client.get("/api/links/refs/stats")).json()

    assert stats["top_referrers"] == [
        {"referrer": "https://a.example/", "count": 4},
        {"referrer": "https://b.example/", "count": 3},
    ]


async def test_a_click_with_no_referrer_rolls_up_as_direct(auth_client):
    await _make_link(auth_client, code="plain")
    link_id = await _link_id("plain")
    await _add_clicks(link_id, days_ago=1, count=2, referrer=None)

    await _fold()

    async with SessionFactory() as session:
        stored = await session.scalar(
            select(ReferrerDaily.referrer).where(ReferrerDaily.link_id == link_id)
        )
    assert stored == ""

    stats = (await auth_client.get("/api/links/plain/stats")).json()
    assert stats["top_referrers"] == [{"referrer": "direct", "count": 2}]


async def test_the_daily_window_still_bounds_the_buckets(auth_client):
    await _make_link(auth_client, code="window")
    link_id = await _link_id("window")
    await _add_clicks(link_id, days_ago=1, count=1)
    await _add_clicks(link_id, days_ago=20, count=1)
    await _fold(days=30)

    stats = (await auth_client.get("/api/links/window/stats?days=7")).json()

    # Both are folded and both count towards the total; only one is inside the window.
    assert stats["total_clicks"] == 2
    assert [row["day"] for row in stats["daily"]] == [(TODAY - timedelta(days=1)).isoformat()]


async def test_deleting_a_link_takes_its_rollups_with_it(auth_client):
    await _make_link(auth_client, code="doomed")
    link_id = await _link_id("doomed")
    await _add_clicks(link_id, days_ago=1, count=2, referrer="https://a.example/")
    await _fold()
    assert await _rollup_rows(link_id) != []

    await auth_client.delete("/api/links/doomed")

    assert await _rollup_rows(link_id) == []
    async with SessionFactory() as session:
        remaining = await session.scalar(
            select(func.count()).select_from(ReferrerDaily).where(ReferrerDaily.link_id == link_id)
        )
    assert remaining == 0


async def test_prune_refuses_a_window_that_would_eat_today():
    async with SessionFactory() as session:
        with pytest.raises(ValueError, match="at least 1"):
            await rollup.prune(session, keep_days=0)


async def test_the_fold_command_runs_end_to_end(auth_client):
    await _make_link(auth_client, code="cli")
    link_id = await _link_id("cli")
    await _add_clicks(link_id, days_ago=1, count=2)

    await rollup.run("fold", days=7)

    assert await _rollup_rows(link_id) == [(TODAY - timedelta(days=1), 2, 1)]


async def test_the_prune_command_runs_end_to_end(auth_client):
    await _make_link(auth_client, code="cli2")
    link_id = await _link_id("cli2")
    await _add_clicks(link_id, days_ago=200, count=2)

    await rollup.run("prune", keep_days=30)

    assert await _raw_count(link_id) == 0
    assert await _rollup_rows(link_id) == [(TODAY - timedelta(days=200), 2, 1)]


def test_the_parser_wires_both_commands_to_their_defaults():
    parser = rollup.build_parser()
    assert parser.parse_args(["fold"]).days == rollup.DEFAULT_FOLD_DAYS
    assert parser.parse_args(["fold", "--days", "3"]).days == 3
    assert parser.parse_args(["prune"]).keep_days == rollup.settings.click_retention_days
    assert parser.parse_args(["prune", "--keep-days", "10"]).keep_days == 10


def test_the_command_line_dispatches_and_then_lets_go_of_the_pool(monkeypatch):
    """main() owns the process: it parses, runs one command, and disposes the engine.

    Both halves are faked out because the point here is the wiring, not the SQL -- the
    SQL has its own tests above.
    """
    calls = []

    async def fake_run(command, days, keep_days):
        calls.append(("run", command, days, keep_days))

    class FakeEngine:
        async def dispose(self):
            calls.append(("disposed",))

    monkeypatch.setattr(rollup, "run", fake_run)
    monkeypatch.setattr(rollup, "engine", FakeEngine())

    rollup.main(["fold", "--days", "2"])
    rollup.main(["prune", "--keep-days", "45"])

    assert calls == [
        ("run", "fold", 2, 0),
        ("disposed",),
        ("run", "prune", rollup.DEFAULT_FOLD_DAYS, 45),
        ("disposed",),
    ]
