from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class MovieBase(BaseModel):
    title: str
    genres: str  # vd: "Action,Sci-Fi,Thriller"
    description: str
    duration_minutes: int
    rating: float | None = None
    poster_url: str | None = None


class MovieCreate(MovieBase):
    """Dữ liệu client gửi lên khi tạo phim mới — giống MovieBase, không cần id."""
    pass


class MovieUpdate(BaseModel):
    """
    Tất cả field đều optional — cho phép client chỉ gửi field muốn sửa
    (PATCH-style update) thay vì phải gửi lại toàn bộ thông tin phim.
    """
    title: str | None = None
    genres: str | None = None
    description: str | None = None
    duration_minutes: int | None = None
    rating: float | None = None
    poster_url: str | None = None


class MovieRead(MovieBase):
    """Dữ liệu trả về cho client — có thêm id so với lúc tạo."""
    id: int
    tmdb_id: int | None = None
    metadata_source: str | None = None
    rating_vote_count: int | None = None
    rating_source: str | None = None

    # from_attributes=True: cho phép Pydantic đọc trực tiếp từ SQLAlchemy
    # model (object có attribute), không chỉ từ dict — cần thiết vì route
    # sẽ trả thẳng object Movie lấy từ database.
    model_config = ConfigDict(from_attributes=True)


class MovieShowtimeRead(BaseModel):
    id: int
    room_id: int | None
    room_name: str | None
    start_time: datetime
    price: Decimal | None
    booking_mode: str
    external_booking_url: str | None = None
    format: str | None = None
    language: str | None = None


class CinemaShowtimes(BaseModel):
    id: int
    name: str
    address: str
    city: str
    showtimes: list[MovieShowtimeRead]


class MovieShowtimeAggregation(BaseModel):
    movie: MovieRead
    cinemas: list[CinemaShowtimes]
