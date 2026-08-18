from datetime import UTC, datetime, timedelta


async def _make_link(auth_client, target="https://example.com/target", code="demo", **extra):
    response = await auth_client.post(
        "/api/links", json={"target_url": target, "custom_code": code, **extra}
    )
    assert response.status_code == 201
    return response.json()


async def test_redirect_points_at_the_target(auth_client, client):
    await _make_link(auth_client)

    response = await client.get("/demo", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://example.com/target"


async def test_unknown_code_is_a_404(client):
    assert (await client.get("/nope", follow_redirects=False)).status_code == 404


async def test_expired_link_is_gone(auth_client, client):
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await _make_link(auth_client, code="stale", expires_at=past)

    response = await client.get("/stale", follow_redirects=False)
    assert response.status_code == 410


async def test_deleted_link_stops_redirecting_even_though_it_was_cached(auth_client, client):
    """Guards the cache-invalidation path: a stale cache entry would still redirect."""
    await _make_link(auth_client, code="cached")
    assert (await client.get("/cached", follow_redirects=False)).status_code == 307

    await auth_client.delete("/api/links/cached")

    assert (await client.get("/cached", follow_redirects=False)).status_code == 404


async def test_clicks_are_recorded_with_referrer(auth_client, client):
    await _make_link(auth_client, code="tracked")

    await client.get(
        "/tracked", follow_redirects=False, headers={"referer": "https://news.ycombinator.com/"}
    )
    await client.get("/tracked", follow_redirects=False)

    stats = (await auth_client.get("/api/links/tracked/stats")).json()
    assert stats["total_clicks"] == 2
    referrers = {row["referrer"]: row["count"] for row in stats["top_referrers"]}
    assert referrers == {"https://news.ycombinator.com/": 1, "direct": 1}


async def test_stats_group_clicks_by_day(auth_client, client):
    await _make_link(auth_client, code="daily")
    await client.get("/daily", follow_redirects=False)

    stats = (await auth_client.get("/api/links/daily/stats")).json()
    assert len(stats["daily"]) == 1
    assert stats["daily"][0]["count"] == 1
    assert stats["daily"][0]["day"] == datetime.now(UTC).date().isoformat()


async def test_stats_count_unique_visitors_separately_from_clicks(auth_client, client):
    await _make_link(auth_client, code="uniq")

    await client.get("/uniq", follow_redirects=False, headers={"x-forwarded-for": "1.1.1.1"})
    await client.get("/uniq", follow_redirects=False, headers={"x-forwarded-for": "1.1.1.1"})
    await client.get("/uniq", follow_redirects=False, headers={"x-forwarded-for": "2.2.2.2"})

    stats = (await auth_client.get("/api/links/uniq/stats")).json()
    assert stats["total_clicks"] == 3
    assert stats["unique_visitors"] == 2


async def test_stats_are_owner_only(auth_client, client):
    await _make_link(auth_client, code="secret")
    assert (await client.get("/api/links/secret/stats")).status_code == 401
