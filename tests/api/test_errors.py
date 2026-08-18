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
