import pytest

from app.core.config import settings
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
