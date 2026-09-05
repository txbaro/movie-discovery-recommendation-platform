import logging
import secrets
from pathlib import Path
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.i18n import get_locale
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
logger = logging.getLogger(__name__)

COOKIE_NAME = "access_token"
RESET_TOKEN_TTL_SECONDS = 15 * 60  # 15 phút
AVATAR_DIRECTORY = Path(__file__).resolve().parent.parent / "static/uploads/avatars"
MAX_AVATAR_SIZE = 3 * 1024 * 1024
AVATAR_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


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


def _avatar_extension(content: bytes) -> str | None:
    for signature, extension in AVATAR_SIGNATURES:
        if content.startswith(signature):
            return extension
    if (
        len(content) >= 12
        and content[:4] == b"RIFF"
        and content[8:12] == b"WEBP"
    ):
        return "webp"
    return None


def _local_avatar_path(avatar_url: str | None) -> Path | None:
    prefix = "/static/uploads/avatars/"
    if not avatar_url or not avatar_url.startswith(prefix):
        return None
    filename = avatar_url.removeprefix(prefix)
    if Path(filename).name != filename:
        return None
    return AVATAR_DIRECTORY / filename


@router.patch("/me", response_model=UserRead)
async def update_me(
    full_name: str = Form(...),
    avatar: UploadFile | None = File(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update profile data and optionally replace the locally stored avatar."""
    name = full_name.strip()
    if not 1 <= len(name) <= 255:
        raise HTTPException(status_code=422, detail="Tên phải có từ 1 đến 255 ký tự")

    new_avatar_url: str | None = None
    if avatar is not None and avatar.filename:
        content = await avatar.read(MAX_AVATAR_SIZE + 1)
        extension = _avatar_extension(content)
        if len(content) > MAX_AVATAR_SIZE:
            raise HTTPException(status_code=413, detail="Ảnh đại diện tối đa 3 MB")
        if extension is None:
            raise HTTPException(
                status_code=422,
                detail="Avatar chỉ hỗ trợ ảnh PNG, JPG, GIF hoặc WebP",
            )
        AVATAR_DIRECTORY.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid4().hex}.{extension}"
        avatar_path = AVATAR_DIRECTORY / filename
        avatar_path.write_bytes(content)
        new_avatar_url = f"/static/uploads/avatars/{filename}"

    old_avatar_path = _local_avatar_path(current_user.avatar_url)
    current_user.full_name = name
    if new_avatar_url is not None:
        current_user.avatar_url = new_avatar_url
    try:
        await db.commit()
        await db.refresh(current_user)
    except Exception:
        await db.rollback()
        if new_avatar_url is not None:
            new_avatar_path = _local_avatar_path(new_avatar_url)
            if new_avatar_path is not None:
                new_avatar_path.unlink(missing_ok=True)
        raise

    if new_avatar_url is not None and old_avatar_path is not None:
        old_avatar_path.unlink(missing_ok=True)
    return current_user


@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Sinh token reset (nếu email tồn tại) và "gửi" link reset.

    QUAN TRỌNG VỀ BẢO MẬT: response LUÔN trả về CÙNG 1 thông báo, bất kể
    email đó có tồn tại trong hệ thống hay không - giống lý do đã áp dụng
    ở /auth/login. Nếu response khác nhau tuỳ email tồn tại/không tồn tại,
    kẻ tấn công có thể dùng endpoint này để dò xem email nào đã đăng ký
    (gọi là "user enumeration attack").

    Production gửi email qua Resend HTTPS API; SMTP chỉ là fallback local.
    Delivery error không được trả về client để tránh làm lộ việc email có tồn
    tại trong hệ thống hay không.
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
            await send_password_reset_email(
                to=payload.email,
                reset_link=reset_link,
                locale=get_locale(request),
            )
        except EmailError as e:
            # KHÔNG raise lỗi ra cho client — nếu SMTP lỗi (config sai, hết
            # quota...), user vẫn nên thấy thông báo thành công như bình
            # thường (tránh lộ thông tin/tránh crash trải nghiệm). Lỗi thật
            # được LOG lại để admin/dev tự biết mà xử lý.
            await redis_client.delete(_reset_token_key(token))
            logger.error(
                "Password reset email delivery failed for user_id=%s: %s",
                user.id,
                e,
            )

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
