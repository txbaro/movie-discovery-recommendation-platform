from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import settings
from app.core.redis_client import redis_client
from app.routes import auth
from app.services.email import EmailError
from tests.conftest import register_and_login


@pytest.mark.asyncio
async def test_register_login_me_and_logout(client):
    user_id = await register_and_login(client, "auth@example.com")
    me = await client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == user_id

    logout = await client.post("/auth/logout")
    assert logout.status_code == 200
    assert (await client.get("/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_duplicate_registration_and_wrong_password(client):
    payload = {
        "email": "duplicate@example.com",
        "full_name": "Duplicate",
        "password": "password123",
    }
    assert (await client.post("/auth/register", json=payload)).status_code == 201
    assert (await client.post("/auth/register", json=payload)).status_code == 400
    wrong = await client.post(
        "/auth/login",
        json={"email": payload["email"], "password": "wrong-password"},
    )
    assert wrong.status_code == 401


@pytest.mark.asyncio
async def test_login_cookie_can_be_secured_for_production(client, monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", True)
    await client.post(
        "/auth/register",
        json={
            "email": "secure-cookie@example.com",
            "full_name": "Secure Cookie",
            "password": "password123",
        },
    )

    response = await client.post(
        "/auth/login",
        json={"email": "secure-cookie@example.com", "password": "password123"},
    )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_user_can_update_name_and_avatar(client, monkeypatch, tmp_path):
    await register_and_login(client, "profile@example.com")
    monkeypatch.setattr(auth, "AVATAR_DIRECTORY", tmp_path)

    response = await client.patch(
        "/auth/me",
        data={"full_name": "Tên mới"},
        files={"avatar": ("avatar.png", b"\x89PNG\r\n\x1a\nimage", "image/png")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["full_name"] == "Tên mới"
    avatar_url = response.json()["avatar_url"]
    assert avatar_url and avatar_url.endswith(".png")
    assert len(list(tmp_path.glob("*.png"))) == 1
    assert (await client.get("/auth/me")).json()["avatar_url"] == avatar_url


@pytest.mark.asyncio
async def test_user_cannot_upload_a_non_image_avatar(client):
    await register_and_login(client, "bad-avatar@example.com")
    response = await client.patch(
        "/auth/me",
        data={"full_name": "Test User"},
        files={"avatar": ("avatar.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_forgot_password_sends_single_use_reset_link(client, monkeypatch):
    await register_and_login(client, "reset@example.com")
    links = []

    async def fake_send_password_reset_email(
        to: str, reset_link: str, locale: str
    ):
        assert to == "reset@example.com"
        assert locale == "en"
        links.append(reset_link)

    monkeypatch.setattr(
        auth,
        "send_password_reset_email",
        fake_send_password_reset_email,
    )
    client.cookies.set("locale", "en")

    response = await client.post(
        "/auth/forgot-password",
        json={"email": "reset@example.com"},
    )
    assert response.status_code == 200
    assert len(links) == 1
    token = parse_qs(urlparse(links[0]).query)["token"][0]

    reset = await client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "new-password-123"},
    )
    assert reset.status_code == 200
    reused = await client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "another-password-123"},
    )
    assert reused.status_code == 400

    login = await client.post(
        "/auth/login",
        json={"email": "reset@example.com", "password": "new-password-123"},
    )
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_forgot_password_hides_unknown_email(client, monkeypatch):
    calls = []

    async def fake_send_password_reset_email(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        auth,
        "send_password_reset_email",
        fake_send_password_reset_email,
    )
    response = await client.post(
        "/auth/forgot-password",
        json={"email": "unknown@example.com"},
    )

    assert response.status_code == 200
    assert calls == []


@pytest.mark.asyncio
async def test_failed_delivery_removes_reset_token(client, monkeypatch):
    await register_and_login(client, "failed-email@example.com")
    links = []

    async def fail_delivery(to: str, reset_link: str, locale: str):
        links.append(reset_link)
        raise EmailError("provider unavailable")

    monkeypatch.setattr(auth, "send_password_reset_email", fail_delivery)
    response = await client.post(
        "/auth/forgot-password",
        json={"email": "failed-email@example.com"},
    )

    assert response.status_code == 200
    token = parse_qs(urlparse(links[0]).query)["token"][0]
    assert await redis_client.get(auth._reset_token_key(token)) is None
