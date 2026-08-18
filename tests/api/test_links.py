async def test_create_link_returns_a_short_url(auth_client):
    response = await auth_client.post(
        "/api/links", json={"target_url": "https://example.com/a-long-article"}
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body["code"]) == 7
    assert body["short_url"].endswith(body["code"])


async def test_create_link_requires_authentication(client):
    response = await client.post("/api/links", json={"target_url": "https://example.com"})
    assert response.status_code == 401


async def test_create_link_rejects_non_http_scheme(auth_client):
    response = await auth_client.post("/api/links", json={"target_url": "javascript:alert(1)"})
    assert response.status_code == 422


async def test_create_link_rejects_self_reference(auth_client):
    response = await auth_client.post("/api/links", json={"target_url": "http://testserver/abc"})
    assert response.status_code == 400


async def test_custom_code_is_used_verbatim(auth_client):
    response = await auth_client.post(
        "/api/links", json={"target_url": "https://example.com", "custom_code": "my-talk"}
    )
    assert response.status_code == 201
    assert response.json()["code"] == "my-talk"


async def test_custom_code_cannot_be_taken_twice(auth_client):
    payload = {"target_url": "https://example.com", "custom_code": "taken"}
    await auth_client.post("/api/links", json=payload)
    response = await auth_client.post("/api/links", json=payload)
    assert response.status_code == 409


async def test_custom_code_cannot_shadow_a_real_route(auth_client):
    response = await auth_client.post(
        "/api/links", json={"target_url": "https://example.com", "custom_code": "docs"}
    )
    assert response.status_code == 409


async def test_custom_code_rejects_unsafe_characters(auth_client):
    response = await auth_client.post(
        "/api/links", json={"target_url": "https://example.com", "custom_code": "a/b?c"}
    )
    assert response.status_code == 422


async def test_list_returns_only_my_links_newest_first(auth_client):
    await auth_client.post(
        "/api/links", json={"target_url": "https://example.com/1", "custom_code": "mine1"}
    )
    await auth_client.post(
        "/api/links", json={"target_url": "https://example.com/2", "custom_code": "mine2"}
    )

    response = await auth_client.get("/api/links")
    assert response.status_code == 200

    page = response.json()
    assert [link["code"] for link in page["items"]] == ["mine2", "mine1"]
    assert page["total"] == 2


async def test_another_user_cannot_see_my_link(auth_client, client):
    await auth_client.post(
        "/api/links", json={"target_url": "https://example.com", "custom_code": "private"}
    )

    await client.post(
        "/auth/register", json={"email": "other@example.com", "password": "supersecret123"}
    )
    token = (
        await client.post(
            "/auth/token", data={"username": "other@example.com", "password": "supersecret123"}
        )
    ).json()["access_token"]

    response = await client.get("/api/links/private", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404


async def test_delete_removes_the_link(auth_client):
    await auth_client.post(
        "/api/links", json={"target_url": "https://example.com", "custom_code": "bye"}
    )
    assert (await auth_client.delete("/api/links/bye")).status_code == 204
    assert (await auth_client.get("/api/links/bye")).status_code == 404


async def test_create_is_rate_limited(auth_client, monkeypatch):
    from app.routers import links

    monkeypatch.setattr(links.settings, "create_limit_per_hour", 2)

    for _ in range(2):
        response = await auth_client.post("/api/links", json={"target_url": "https://example.com"})
        assert response.status_code == 201

    response = await auth_client.post("/api/links", json={"target_url": "https://example.com"})
    assert response.status_code == 429
    assert "Retry-After" in response.headers


async def test_qr_returns_a_png_by_default(auth_client):
    await auth_client.post(
        "/api/links", json={"target_url": "https://example.com", "custom_code": "qr1"}
    )

    response = await auth_client.get("/api/links/qr1/qr")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


async def test_qr_can_return_svg(auth_client):
    await auth_client.post(
        "/api/links", json={"target_url": "https://example.com", "custom_code": "qr2"}
    )

    response = await auth_client.get("/api/links/qr2/qr", params={"format": "svg"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert b"<svg" in response.content


async def test_qr_rejects_an_unknown_format(auth_client):
    await auth_client.post(
        "/api/links", json={"target_url": "https://example.com", "custom_code": "qr3"}
    )

    response = await auth_client.get("/api/links/qr3/qr", params={"format": "gif"})
    assert response.status_code == 422


async def test_qr_rejects_an_absurd_box_size(auth_client):
    await auth_client.post(
        "/api/links", json={"target_url": "https://example.com", "custom_code": "qr4"}
    )

    response = await auth_client.get("/api/links/qr4/qr", params={"box_size": 5000})
    assert response.status_code == 422


async def test_qr_is_owner_only(auth_client, client):
    await auth_client.post(
        "/api/links", json={"target_url": "https://example.com", "custom_code": "qr5"}
    )

    assert (await client.get("/api/links/qr5/qr")).status_code == 401


async def test_page_reports_the_total_beyond_the_current_slice(auth_client):
    for i in range(5):
        await auth_client.post(
            "/api/links", json={"target_url": "https://example.com", "custom_code": f"page{i}"}
        )

    page = (await auth_client.get("/api/links", params={"limit": 2})).json()
    assert len(page["items"]) == 2
    assert page["total"] == 5
    assert page["limit"] == 2
    assert page["offset"] == 0


async def test_offset_walks_through_the_pages(auth_client):
    for i in range(3):
        await auth_client.post(
            "/api/links", json={"target_url": "https://example.com", "custom_code": f"walk{i}"}
        )

    first = (await auth_client.get("/api/links", params={"limit": 2, "offset": 0})).json()
    second = (await auth_client.get("/api/links", params={"limit": 2, "offset": 2})).json()

    codes = [link["code"] for link in first["items"] + second["items"]]
    assert codes == ["walk2", "walk1", "walk0"]


async def test_patch_repoints_a_link(auth_client):
    await auth_client.post(
        "/api/links", json={"target_url": "https://old.example", "custom_code": "moved"}
    )

    response = await auth_client.patch(
        "/api/links/moved", json={"target_url": "https://new.example"}
    )
    assert response.status_code == 200
    assert response.json()["target_url"] == "https://new.example"
    assert response.json()["code"] == "moved"


async def test_patch_leaves_untouched_fields_alone(auth_client):
    await auth_client.post(
        "/api/links", json={"target_url": "https://keep.example", "custom_code": "partial"}
    )

    response = await auth_client.patch("/api/links/partial", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["target_url"] == "https://keep.example"
    assert response.json()["is_active"] is False


async def test_patch_rejects_an_unsafe_target(auth_client):
    await auth_client.post(
        "/api/links", json={"target_url": "https://ok.example", "custom_code": "guarded"}
    )

    response = await auth_client.patch(
        "/api/links/guarded", json={"target_url": "http://169.254.169.254/"}
    )
    assert response.status_code == 422


async def test_patch_is_owner_only(auth_client, client):
    await auth_client.post(
        "/api/links", json={"target_url": "https://example.com", "custom_code": "notyours"}
    )

    response = await client.patch("/api/links/notyours", json={"is_active": False})
    assert response.status_code == 401


async def test_patch_on_a_missing_link_is_a_404(auth_client):
    assert (
        await auth_client.patch("/api/links/ghost", json={"is_active": False})
    ).status_code == 404
