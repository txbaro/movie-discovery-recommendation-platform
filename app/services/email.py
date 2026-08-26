"""
Service gửi email qua SMTP — dùng aiosmtplib (async) để không chặn event
loop, giữ đúng kiến trúc async xuyên suốt project (giống lý do chọn
async SQLAlchemy, httpx async ở các phần trước).

Viết theo kiểu GENERIC: hoạt động với bất kỳ dịch vụ SMTP chuẩn nào
(Gmail, SendGrid, Mailgun, Resend, Mailtrap...) — chỉ cần đúng host/port/
username/password trong .env, không cần đổi code khi đổi nhà cung cấp.
"""
import aiosmtplib
from email.message import EmailMessage

from app.core.config import settings


class EmailError(Exception):
    """Raise khi gửi email thất bại — nơi gọi tự quyết định xử lý (vd log
    lỗi nhưng vẫn trả response thành công cho user, tránh lộ thông tin)."""
    pass


async def send_email(to: str, subject: str, html_body: str) -> None:
    if not settings.SMTP_HOST:
        raise EmailError(
            "Chưa cấu hình SMTP trong .env (SMTP_HOST đang trống). "
            "Xem hướng dẫn trong .env.example."
        )

    message = EmailMessage()
    message["From"] = settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME
    message["To"] = to
    message["Subject"] = subject
    message.set_content("Email này cần trình đọc email hỗ trợ HTML.")
    message.add_alternative(html_body, subtype="html")

    await aiosmtplib.send(
        message,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USERNAME,
        password=settings.SMTP_PASSWORD,
        start_tls=settings.SMTP_USE_TLS,
    )


async def send_password_reset_email(to: str, reset_link: str) -> None:
    """
    Template email cụ thể cho tính năng quên mật khẩu — tách riêng khỏi
    send_email() để sau này thêm template khác (vd email xác nhận vé)
    không phải sửa hàm gửi email gốc.
    """
    html_body = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>Đặt lại mật khẩu</h2>
        <p>Bạn (hoặc ai đó) đã yêu cầu đặt lại mật khẩu cho tài khoản này.</p>
        <p>
            <a href="{reset_link}"
               style="display:inline-block; padding:12px 24px; background:#e50914;
                      color:white; text-decoration:none; border-radius:6px;">
                Đặt lại mật khẩu
            </a>
        </p>
        <p style="color:#6b6b6b; font-size:0.9em;">
            Link này hết hạn sau 15 phút. Nếu bạn không yêu cầu, hãy bỏ qua email này.
        </p>
    </div>
    """
    await send_email(
        to=to,
        subject="Đặt lại mật khẩu - Movie Discovery",
        html_body=html_body,
    )
