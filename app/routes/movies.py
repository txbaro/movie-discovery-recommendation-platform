from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.movie import Movie
from app.models.cinema import Cinema
from app.models.cinema_room import CinemaRoom
from app.models.showtime import Showtime
from app.schemas.movie import (
    CinemaShowtimes,
    MovieCreate,
    MovieRead,
    MovieShowtimeAggregation,
    MovieShowtimeRead,
    MovieUpdate,
)
from app.services.tmdb import TMDBError, fetch_movies
from app.services.discovery import utc_now, vietnamese_date_range

router = APIRouter(prefix="/movies", tags=["movies"])


@router.post("", response_model=MovieRead, status_code=status.HTTP_201_CREATED)
async def create_movie(payload: MovieCreate, db: AsyncSession = Depends(get_db)):
    movie = Movie(**payload.model_dump())
    db.add(movie)
    await db.commit()
    await db.refresh(movie)
    return movie


@router.get("", response_model=list[MovieRead])
async def list_movies(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    genre: str | None = None,
    title: str | None = None,
    source: str | None = None,
    available_only: bool = False,
    db: AsyncSession = Depends(get_db),
):

    query = select(Movie)
    if genre:
        query = query.where(Movie.genres.ilike(f"%{genre}%"))
    if title:
        query = query.where(Movie.title.ilike(f"%{title}%"))
    if source:
        query = query.where(
            select(Showtime.id)
            .where(Showtime.movie_id == Movie.id, Showtime.source == source)
            .exists()
        )
    if available_only:
        query = query.where(
            select(Showtime.id)
            .where(
                Showtime.movie_id == Movie.id,
                Showtime.start_time >= utc_now(),
            )
            .exists()
        )
    query = query.order_by(Movie.title, Movie.id).offset(skip).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{movie_id}", response_model=MovieRead)
async def get_movie(movie_id: int, db: AsyncSession = Depends(get_db)):
    movie = await db.get(Movie, movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phim")
    return movie


@router.get("/{movie_id}/showtimes", response_model=MovieShowtimeAggregation)
async def aggregate_movie_showtimes(
    movie_id: int,
    city: str | None = None,
    source: str | None = None,
    show_date: date | None = Query(default=None, alias="date"),
    upcoming_only: bool = True,
    db: AsyncSession = Depends(get_db),
):
    movie = await db.get(Movie, movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phim")

    query = (
        select(Showtime, CinemaRoom, Cinema)
        .outerjoin(CinemaRoom, Showtime.room_id == CinemaRoom.id)
        .join(Cinema, Showtime.cinema_id == Cinema.id)
        .where(Showtime.movie_id == movie_id)
        .order_by(Cinema.name, Showtime.start_time)
    )
    if city:
        query = query.where(Cinema.city.ilike(f"%{city}%"))
    if source:
        query = query.where(Showtime.source == source)
    if show_date is not None:
        start, end = vietnamese_date_range(show_date)
        query = query.where(
            Showtime.start_time >= start,
            Showtime.start_time < end,
        )
    if upcoming_only:
        query = query.where(Showtime.start_time >= utc_now())

    grouped: dict[int, CinemaShowtimes] = {}
    for showtime, room, cinema in (await db.execute(query)).all():
        cinema_item = grouped.get(cinema.id)
        if cinema_item is None:
            cinema_item = CinemaShowtimes(
                id=cinema.id,
                name=cinema.name,
                address=cinema.address,
                city=cinema.city,
                showtimes=[],
            )
            grouped[cinema.id] = cinema_item
        cinema_item.showtimes.append(
            MovieShowtimeRead(
                id=showtime.id,
                room_id=room.id if room else None,
                room_name=room.name if room else None,
                start_time=showtime.start_time,
                price=showtime.price,
                booking_mode=showtime.booking_mode,
                external_booking_url=showtime.external_booking_url,
                format=showtime.format,
                language=showtime.language,
            )
        )
    return MovieShowtimeAggregation(
        movie=MovieRead.model_validate(movie),
        cinemas=list(grouped.values()),
    )


@router.patch("/{movie_id}", response_model=MovieRead)
async def update_movie(
    movie_id: int, payload: MovieUpdate, db: AsyncSession = Depends(get_db)
):
    movie = await db.get(Movie, movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phim")

    # exclude_unset=True: chỉ lấy field mà client THỰC SỰ gửi lên,
    # bỏ qua field không gửi (giữ nguyên giá trị cũ trong DB)
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(movie, field, value)

    await db.commit()
    await db.refresh(movie)
    return movie


@router.delete("/{movie_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_movie(movie_id: int, db: AsyncSession = Depends(get_db)):
    movie = await db.get(Movie, movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phim")

    await db.delete(movie)
    await db.commit()

@router.post("/import-from-tmdb", response_model=list[MovieRead])
async def import_from_tmdb(
    category: str = "popular",
    page: int = 1,
    db: AsyncSession = Depends(get_db),
):
    """
    Kéo dữ liệu phim thật từ TMDB về database — dùng để có data mẫu nhanh
    thay vì gõ tay từng phim qua POST /movies.

    category: "popular" | "now_playing" | "top_rated" | "upcoming"
    page: TMDB trả 20 phim/trang, đổi page để lấy thêm phim khác.

    Phim đã tồn tại (trùng tmdb_id) sẽ được BỎ QUA, không tạo trùng —
    an toàn khi gọi lại API này nhiều lần.
    """
    if category not in {"popular", "now_playing", "top_rated", "upcoming"}:
        raise HTTPException(
            status_code=400,
            detail="category phải là: popular, now_playing, top_rated, hoặc upcoming",
        )

    try:
        tmdb_movies = await fetch_movies(category=category, page=page)
    except TMDBError as e:
        # 502 Bad Gateway: lỗi đến từ dịch vụ bên ngoài (TMDB), không phải
        # lỗi của server mình — mã lỗi này phản ánh đúng bản chất vấn đề.
        raise HTTPException(status_code=502, detail=str(e))

    # Lấy trước danh sách tmdb_id đã có trong DB để lọc phim trùng
    # bằng 1 query duy nhất, thay vì query riêng cho từng phim (tránh N+1).
    incoming_ids = [m["tmdb_id"] for m in tmdb_movies]
    existing_result = await db.execute(
        select(Movie.tmdb_id).where(Movie.tmdb_id.in_(incoming_ids))
    )
    existing_ids = {row[0] for row in existing_result.all()}

    new_movies = [
        Movie(**m) for m in tmdb_movies if m["tmdb_id"] not in existing_ids
    ]

    if new_movies:
        db.add_all(new_movies)
        await db.commit()
        for m in new_movies:
            await db.refresh(m)

    return new_movies
