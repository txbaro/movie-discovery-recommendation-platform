import json

import httpx
import pytest

from app.core.config import settings
from app.services import email
from app.services.email import EmailError


@pytest.mark.asyncio
async def test_resend_email_uses_https_api_contract(monkeypatch):
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == "https://api.resend.test/emails"
        assert request.headers["authorization"] == "Bearer re_test_key"
        assert json.loads(request.content) == {
            "from": "Movie Discovery <noreply@example.com>",
            "to": ["viewer@example.com"],
            "subject": "Reset password",
            "html": "<p>Reset</p>",
        }
        return httpx.Response(200, json={"id": "email-id"})

    def fake_async_client(*_args, **kwargs):
        return real_async_client(
            transport=httpx.MockTransport(handler),
            timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "RESEND_API_BASE_URL", "https://api.resend.test")
    monkeypatch.setattr(
        settings,
        "EMAIL_FROM",
        "Movie Discovery <noreply@example.com>",
    )
    monkeypatch.setattr(email.httpx, "AsyncClient", fake_async_client)

    await email.send_email(
        "viewer@example.com",
        "Reset password",
        "<p>Reset</p>",
    )


@pytest.mark.asyncio
async def test_resend_errors_are_normalized(monkeypatch):
    real_async_client = httpx.AsyncClient

    def fake_async_client(*_args, **kwargs):
        return real_async_client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    403,
                    json={"message": "The sender domain is not verified"},
                )
            ),
            timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "EMAIL_FROM", "noreply@example.com")
    monkeypatch.setattr(email.httpx, "AsyncClient", fake_async_client)

    with pytest.raises(EmailError, match="sender domain is not verified"):
        await email.send_email("viewer@example.com", "Subject", "<p>Body</p>")


@pytest.mark.asyncio
async def test_email_falls_back_to_smtp_locally(monkeypatch):
    calls = []

    async def fake_smtp_send(message, **kwargs):
        calls.append((message, kwargs))

    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "sender@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(email.aiosmtplib, "send", fake_smtp_send)

    await email.send_email("viewer@example.com", "Subject", "<p>Body</p>")

    assert len(calls) == 1
    message, kwargs = calls[0]
    assert message["To"] == "viewer@example.com"
    assert kwargs["hostname"] == "smtp.example.com"


@pytest.mark.asyncio
async def test_password_reset_email_uses_selected_language(monkeypatch):
    captured = {}

    async def fake_send_email(to: str, subject: str, html_body: str):
        captured.update(to=to, subject=subject, html=html_body)

    monkeypatch.setattr(email, "send_email", fake_send_email)
    await email.send_password_reset_email(
        "viewer@example.com",
        "https://example.com/reset?token=secret",
        locale="en",
    )

    assert captured["subject"] == "Reset your password - Movie Discovery"
    assert "This link expires in 15 minutes" in captured["html"]
