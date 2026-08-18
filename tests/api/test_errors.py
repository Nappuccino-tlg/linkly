async def test_not_found_uses_the_error_envelope(client):
    response = await client.get("/nope", follow_redirects=False)
    assert response.status_code == 404

    error = response.json()["error"]
    assert error["status"] == 404
    assert error["message"] == "Link not found"
    assert error["request_id"] == response.headers["x-request-id"]


async def test_validation_failures_name_the_field(client):
    response = await client.post("/auth/register", json={"email": "not-an-email", "password": "x"})
    assert response.status_code == 422

    error = response.json()["error"]
    assert error["message"] == "Request validation failed"
    fields = {detail["field"] for detail in error["details"]}
    assert fields == {"email", "password"}


async def test_unauthorised_keeps_the_www_authenticate_header(client):
    """The envelope must not swallow headers the HTTP spec requires."""
    response = await client.get("/auth/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["status"] == 401


async def test_private_targets_are_refused(auth_client):
    for target in (
        "http://localhost:8000/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/",
    ):
        response = await auth_client.post("/api/links", json={"target_url": target})
        assert response.status_code == 422, target
        assert response.json()["error"]["details"][0]["field"] == "target_url"


async def test_credentials_in_a_target_are_refused(auth_client):
    response = await auth_client.post(
        "/api/links", json={"target_url": "http://apple.com@evil.example/"}
    )
    assert response.status_code == 422


async def test_the_request_id_header_is_sent_exactly_once(client):
    """The handlers set it and the middleware sets it; only one may reach the wire."""
    for path in ("/healthz", "/auth/me", "/nope"):
        response = await client.get(path, follow_redirects=False)
        assert "," not in response.headers["x-request-id"], path


async def test_an_unexpected_failure_still_hands_back_a_request_id(tolerant_client, monkeypatch):
    from app.routers import auth

    await tolerant_client.post(
        "/auth/register", json={"email": "boom@example.com", "password": "supersecret123"}
    )

    def explode(*_args, **_kwargs):
        raise RuntimeError("pretend the database fell over")

    monkeypatch.setattr(auth, "verify_password", explode)

    response = await tolerant_client.post(
        "/auth/token", data={"username": "boom@example.com", "password": "supersecret123"}
    )
    assert response.status_code == 500

    error = response.json()["error"]
    assert error["message"] == "Internal server error"
    assert error["request_id"] == response.headers["x-request-id"]
    # The cause belongs in the log, not in the reply.
    assert "database fell over" not in response.text
