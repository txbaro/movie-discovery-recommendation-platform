"""
Thiết lập kết nối database async với SQLAlchemy 2.0.

Lưu ý quan trọng: vì FastAPI là async, ta dùng AsyncEngine + AsyncSession
thay vì Session đồng bộ như các tutorial SQLAlchemy cũ hay dạy.
"""
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Class cha cho tất cả model — SQLAlchemy 2.0 style."""
    pass


# echo=True sẽ in ra mọi câu SQL được chạy — hữu ích lúc học/debug,
# nên tắt (False) khi deploy thật vì làm log rối và ảnh hưởng hiệu năng.
engine = create_async_engine(settings.DATABASE_URL, echo=settings.SQL_ECHO)

# Factory để tạo session mới cho mỗi request.
# expire_on_commit=False giúp object vẫn dùng được sau khi commit
# (tránh phải query lại khi trả response).
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db():
    """
    Dependency dùng trong route: Depends(get_db).
    FastAPI sẽ tự mở session mới cho mỗi request và đóng lại sau khi xong,
    kể cả khi có exception xảy ra (nhờ try/finally).
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
