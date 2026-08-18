async def test_root_sends_you_to_the_dashboard(client):
    response = await client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/app/"


async def test_dashboard_is_served(client):
    response = await client.get("/app/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Linkly" in response.text


async def test_dashboard_assets_are_served(client):
    assert (await client.get("/app/app.js")).status_code == 200
    assert (await client.get("/app/styles.css")).status_code == 200


async def test_favicon_does_not_fall_through_to_the_redirect_route(client):
    """Browsers ask for this unprompted; without its own route it costs a database lookup."""
    response = await client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert "max-age" in response.headers["cache-control"]


async def test_the_dashboard_path_cannot_be_claimed_as_a_code(auth_client):
    response = await auth_client.post(
        "/api/links", json={"target_url": "https://example.com", "custom_code": "app"}
    )
    assert response.status_code == 409


async def test_the_dashboard_declares_a_strict_policy(client):
    """The page holds a bearer token, so it should not be able to load foreign script."""
    body = (await client.get("/app/")).text
    assert "Content-Security-Policy" in body
    assert "script-src 'self'" in body
    # The QR code arrives as a blob, so that one source has to be allowed.
    assert "img-src 'self' blob:" in body


async def test_the_dashboard_carries_no_inline_styles(client):
    """Inline style attributes would need 'unsafe-inline', which defeats the policy."""
    assert 'style="' not in (await client.get("/app/")).text
