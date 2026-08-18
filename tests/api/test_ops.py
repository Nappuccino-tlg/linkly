async def test_healthz_is_up(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_reports_on_both_dependencies(client):
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": "ok", "redis": "ok"}


async def test_every_response_carries_a_request_id(client):
    response = await client.get("/healthz")
    assert response.headers["x-request-id"]


async def test_a_supplied_request_id_is_kept(client):
    response = await client.get("/healthz", headers={"x-request-id": "trace-abc-123"})
    assert response.headers["x-request-id"] == "trace-abc-123"


async def test_a_junk_request_id_is_replaced(client):
    """Client input ends up in log lines, so it is constrained before it is echoed."""
    response = await client.get("/healthz", headers={"x-request-id": "a b'; DROP TABLE --"})
    assert response.headers["x-request-id"] != "a b'; DROP TABLE --"


async def test_an_overlong_request_id_is_replaced(client):
    response = await client.get("/healthz", headers={"x-request-id": "x" * 200})
    assert len(response.headers["x-request-id"]) <= 64
