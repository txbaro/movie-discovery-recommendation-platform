"""
Service gửi email qua SMTP — dùng aiosmtplib (async) để không chặn event
loop, giữ đúng kiến trúc async xuyên suốt project (giống lý do chọn
async SQLAlchemy, httpx async ở các phần trước).

Viết theo kiểu GENERIC: hoạt động với bất kỳ dịch vụ SMTP chuẩn nào
(Gmail, SendGrid, Mailgun, Resend, Mailtrap...) — chỉ cần đúng host/port/
username/password trong .env, không cần đổi code khi đổi nhà cung cấp.
"""
from email.message import EmailMessage
from html import escape

import aiosmtplib
import httpx
from aiosmtplib.errors import SMTPException

from app.core.config import settings
from app.core.i18n import translate


class EmailError(Exception):
    """Raise khi gửi email thất bại — nơi gọi tự quyết định xử lý (vd log
    lỗi nhưng vẫn trả response thành công cho user, tránh lộ thông tin)."""
    pass


async def _send_with_resend(to: str, subject: str, html_body: str) -> None:
    if not settings.EMAIL_FROM:
        raise EmailError(
            "RESEND_API_KEY đã được cấu hình nhưng EMAIL_FROM đang trống"
        )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{settings.RESEND_API_BASE_URL.rstrip('/')}/emails",
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.EMAIL_FROM,
                    "to": [to],
                    "subject": subject,
                    "html": html_body,
                },
            )
    except httpx.HTTPError as exc:
        raise EmailError("Không thể kết nối Resend Email API") from exc

    if not response.is_success:
        try:
            detail = response.json().get("message") or "unknown error"
        except (ValueError, AttributeError):
            detail = "unknown error"
        raise EmailError(f"Resend từ chối email ({response.status_code}): {detail}")

    try:
        message_id = response.json().get("id")
    except (ValueError, AttributeError) as exc:
        raise EmailError("Resend trả về response không hợp lệ") from exc
    if not message_id:
        raise EmailError("Resend không trả về email id")


async def _send_with_smtp(to: str, subject: str, html_body: str) -> None:
    if not settings.SMTP_HOST:
        raise EmailError("Chưa cấu hình Resend API hoặc SMTP")

    message = EmailMessage()
    message["From"] = settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME
    message["To"] = to
    message["Subject"] = subject
    message.set_content("Email này cần trình đọc email hỗ trợ HTML.")
    message.add_alternative(html_body, subtype="html")

    try:
        await aiosmtplib.send(
            message,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USERNAME,
            password=settings.SMTP_PASSWORD,
            start_tls=settings.SMTP_USE_TLS,
        )
    except (SMTPException, OSError) as exc:
        raise EmailError("Không thể gửi email qua SMTP") from exc


async def send_email(to: str, subject: str, html_body: str) -> None:
    """Prefer HTTPS email delivery; retain SMTP for local/paid environments."""
    if settings.RESEND_API_KEY:
        await _send_with_resend(to, subject, html_body)
        return
    await _send_with_smtp(to, subject, html_body)


async def send_password_reset_email(
    to: str,
    reset_link: str,
    locale: str = "vi",
) -> None:
    """
    Template email cụ thể cho tính năng quên mật khẩu — tách riêng khỏi
    send_email() để sau này thêm template khác (vd email xác nhận vé)
    không phải sửa hàm gửi email gốc.
    """
    html_body = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>{translate(locale, "email.reset_heading")}</h2>
        <p>{translate(locale, "email.reset_body")}</p>
        <p>
            <a href="{escape(reset_link, quote=True)}"
               style="display:inline-block; padding:12px 24px; background:#e50914;
                      color:white; text-decoration:none; border-radius:6px;">
                {translate(locale, "email.reset_action")}
            </a>
        </p>
        <p style="color:#6b6b6b; font-size:0.9em;">
            {translate(locale, "email.reset_expiry")}
        </p>
    </div>
    """
    await send_email(
        to=to,
        subject=translate(locale, "email.reset_subject"),
        html_body=html_body,
    )
