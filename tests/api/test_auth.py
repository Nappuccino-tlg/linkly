import asyncio

from app import ratelimit
from app.cache import redis
from app.routers import auth


async def test_register_returns_user_without_password(client):
    response = await client.post(
        "/auth/register", json={"email": "a@example.com", "password": "supersecret123"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "a@example.com"
    assert "password" not in body and "password_hash" not in body


async def test_register_rejects_duplicate_email(client):
    payload = {"email": "dup@example.com", "password": "supersecret123"}
    await client.post("/auth/register", json=payload)
    response = await client.post("/auth/register", json=payload)
    assert response.status_code == 409


async def test_register_rejects_short_password(client):
    response = await client.post(
        "/auth/register", json={"email": "b@example.com", "password": "short"}
    )
    assert response.status_code == 422


async def test_login_returns_token(client):
    await client.post(
        "/auth/register", json={"email": "c@example.com", "password": "supersecret123"}
    )
    response = await client.post(
        "/auth/token", data={"username": "c@example.com", "password": "supersecret123"}
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"


async def test_login_with_wrong_password_is_rejected(client):
    await client.post(
        "/auth/register", json={"email": "d@example.com", "password": "supersecret123"}
    )
    response = await client.post(
        "/auth/token", data={"username": "d@example.com", "password": "wrongpassword"}
    )
    assert response.status_code == 401


async def test_login_for_unknown_email_looks_identical_to_wrong_password(client):
    response = await client.post(
        "/auth/token", data={"username": "ghost@example.com", "password": "supersecret123"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Incorrect email or password"


async def test_me_requires_a_token(client):
    assert (await client.get("/auth/me")).status_code == 401


async def test_me_rejects_a_garbage_token(client):
    client.headers["Authorization"] = "Bearer not-a-real-token"
    assert (await client.get("/auth/me")).status_code == 401


async def test_me_returns_the_current_user(auth_client):
    response = await auth_client.get("/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "owner@example.com"


async def test_repeated_bad_passwords_are_throttled(client, monkeypatch):
    """Without this, a password is only as strong as bcrypt is slow."""
    monkeypatch.setattr(auth.settings, "login_limit_per_window", 3)
    await client.post(
        "/auth/register", json={"email": "target@example.com", "password": "supersecret123"}
    )

    attempt = {"username": "target@example.com", "password": "wrongpassword"}
    for _ in range(3):
        assert (await client.post("/auth/token", data=attempt)).status_code == 401

    blocked = await client.post("/auth/token", data=attempt)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]


async def test_a_correct_password_is_refused_once_the_budget_is_spent(client, monkeypatch):
    """The lockout is on the account, not on the guess -- knowing the password comes too late."""
    monkeypatch.setattr(auth.settings, "login_limit_per_window", 2)
    await client.post(
        "/auth/register", json={"email": "locked@example.com", "password": "supersecret123"}
    )

    for _ in range(2):
        await client.post(
            "/auth/token", data={"username": "locked@example.com", "password": "nope"}
        )

    response = await client.post(
        "/auth/token", data={"username": "locked@example.com", "password": "supersecret123"}
    )
    assert response.status_code == 429


async def test_signing_in_correctly_costs_nothing(client, monkeypatch):
    """Only failures are counted, so no amount of ordinary use locks a user out."""
    monkeypatch.setattr(auth.settings, "login_limit_per_window", 3)
    await client.post(
        "/auth/register", json={"email": "busy@example.com", "password": "supersecret123"}
    )

    good = {"username": "busy@example.com", "password": "supersecret123"}
    for _ in range(6):
        assert (await client.post("/auth/token", data=good)).status_code == 200


async def test_guessing_one_account_does_not_lock_out_another(client, monkeypatch):
    """The per-email bucket has to be per email, or one victim takes everyone down with them.

    The attempts come from different addresses, so the per-IP bucket cannot be what
    answers here -- otherwise the test would pass without the per-email key existing.
    """
    monkeypatch.setattr(auth.settings, "login_limit_per_window", 2)
    for email in ("victim@example.com", "bystander@example.com"):
        await client.post("/auth/register", json={"email": email, "password": "supersecret123"})

    for _ in range(3):
        await client.post(
            "/auth/token",
            data={"username": "victim@example.com", "password": "nope"},
            headers={"x-forwarded-for": "203.0.113.10"},
        )

    response = await client.post(
        "/auth/token",
        data={"username": "bystander@example.com", "password": "supersecret123"},
        headers={"x-forwarded-for": "203.0.113.11"},
    )
    assert response.status_code == 200


async def test_registration_is_capped_per_address(client, monkeypatch):
    monkeypatch.setattr(auth.settings, "register_limit_per_hour", 2)

    for index in range(2):
        response = await client.post(
            "/auth/register",
            json={"email": f"bulk{index}@example.com", "password": "supersecret123"},
        )
        assert response.status_code == 201

    blocked = await client.post(
        "/auth/register", json={"email": "bulk2@example.com", "password": "supersecret123"}
    )
    assert blocked.status_code == 429


async def test_the_sign_in_bucket_ignores_case_and_padding_in_the_email(client, monkeypatch):
    """Otherwise ' Victim@Example.com ' is a fresh budget for the same account."""
    monkeypatch.setattr(auth.settings, "login_limit_per_window", 2)
    await client.post(
        "/auth/register", json={"email": "case@example.com", "password": "supersecret123"}
    )

    for _ in range(2):
        await client.post(
            "/auth/token",
            data={"username": "case@example.com", "password": "no"},
            headers={"x-forwarded-for": "203.0.113.20"},
        )

    # From a different address, so only the email bucket can be what refuses this.
    response = await client.post(
        "/auth/token",
        data={"username": "  CASE@Example.com  ", "password": "no"},
        headers={"x-forwarded-for": "203.0.113.21"},
    )
    assert response.status_code == 429


async def test_a_burst_of_parallel_guesses_cannot_outrun_the_limit(client, monkeypatch):
    """The reason the limiter spends before it decides, rather than after.

    Sent one after another, eight guesses against a limit of two get two tries. Sent
    together at a limiter that reads the counter, checks a password, and only then
    increments, all eight read the same zero and all eight get through -- and the number
    in the config stops describing anything. INCR is atomic, so they get eight distinct
    numbers instead and only two of them are under the limit.
    """
    monkeypatch.setattr(auth.settings, "login_limit_per_window", 2)
    await client.post(
        "/auth/register", json={"email": "burst@example.com", "password": "supersecret123"}
    )

    attempt = {"username": "burst@example.com", "password": "wrongpassword"}
    responses = await asyncio.gather(*(client.post("/auth/token", data=attempt) for _ in range(8)))

    codes = [response.status_code for response in responses]
    assert codes.count(401) == 2
    assert codes.count(429) == 6


async def test_a_refund_does_not_resurrect_an_expired_window():
    """A bare DECR would recreate the key at -1 with no TTL, and it would then sit there
    absorbing the next window's failures until something noticed."""
    bucket = "login:ip:nobody-was-here"
    await ratelimit.refund([bucket], 900)

    assert await redis.exists(ratelimit._key(bucket, 900)) == 0
