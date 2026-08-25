import secrets

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.redis_client import redis_client
from app.core.security import create_access_token, hash_password, verify_password
from app.services.email import EmailError, send_password_reset_email
from app.models.user import User
from app.schemas.user import (
    ForgotPasswordRequest,
    ResetPasswordRequest,
    UserCreate,
    UserLogin,
    UserRead,
)

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "access_token"
RESET_TOKEN_TTL_SECONDS = 15 * 60  # 15 phút


def _reset_token_key(token: str) -> str:
    return f"password_reset:{token}"


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Email đã được đăng ký")

    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=UserRead)
async def login(payload: UserLogin, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng")

    token = create_access_token(data={"sub": str(user.id)})
    response.set_cookie(
        key=COOKIE_NAME, value=token, httponly=True, samesite="lax",
        secure=settings.COOKIE_SECURE, max_age=60 * 60,
    )
    return user


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"message": "Đã đăng xuất"}


@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)
):
    """
    Sinh token reset (nếu email tồn tại) và "gửi" link reset.

    QUAN TRỌNG VỀ BẢO MẬT: response LUÔN trả về CÙNG 1 thông báo, bất kể
    email đó có tồn tại trong hệ thống hay không - giống lý do đã áp dụng
    ở /auth/login. Nếu response khác nhau tuỳ email tồn tại/không tồn tại,
    kẻ tấn công có thể dùng endpoint này để dò xem email nào đã đăng ký
    (gọi là "user enumeration attack").

    CHƯA CÓ DỊCH VỤ EMAIL THẬT: vì project chưa cấu hình SMTP, link reset
    được IN RA CONSOLE LOG thay vì gửi email thật - đủ để bạn tự test được
    luồng đầy đủ. Xem ghi chú cách gắn email thật ở README.
    """
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if user is not None:
        token = secrets.token_urlsafe(32)
        await redis_client.set(
            _reset_token_key(token), str(user.id), ex=RESET_TOKEN_TTL_SECONDS
        )
        reset_link = (
            f"{settings.APP_BASE_URL.rstrip('/')}/reset-password?token={token}"
        )

        try:
            await send_password_reset_email(to=payload.email, reset_link=reset_link)
        except EmailError as e:
            # KHÔNG raise lỗi ra cho client — nếu SMTP lỗi (config sai, hết
            # quota...), user vẫn nên thấy thông báo thành công như bình
            # thường (tránh lộ thông tin/tránh crash trải nghiệm). Lỗi thật
            # được LOG lại để admin/dev tự biết mà xử lý.
            print(f"[LỖI GỬI EMAIL] {e}")

    return {
        "message": "Nếu email tồn tại trong hệ thống, link đặt lại mật khẩu đã được gửi."
    }


@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)
):
    key = _reset_token_key(payload.token)
    user_id = await redis_client.get(key)

    if user_id is None:
        raise HTTPException(
            status_code=400, detail="Token không hợp lệ hoặc đã hết hạn"
        )

    user = await db.get(User, int(user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy user")

    user.hashed_password = hash_password(payload.new_password)
    await db.commit()

    await redis_client.delete(key)

    return {"message": "Đặt lại mật khẩu thành công, hãy đăng nhập lại."}
