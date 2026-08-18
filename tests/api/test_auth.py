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
