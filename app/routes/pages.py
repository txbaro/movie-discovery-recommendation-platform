"""
Route render HTML bằng Jinja2 — khác với các route JSON API (movies.py,
booking.py...), các route ở đây trả về HTML để hiển thị trực tiếp trên
trình duyệt. Logic nghiệp vụ (đặt vé, đăng nhập...) vẫn dùng LẠI các API
JSON đã xây, gọi qua JavaScript fetch() từ phía client.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.templates import templates
from app.core.redis_client import redis_client
from app.models.movie import Movie
from app.models.cinema import Cinema
from app.models.cinema_room import CinemaRoom
from app.models.showtime import Showtime
from app.models.showtime_seat import ShowtimeSeat
from app.models.seat import Seat
from app.schemas.showtime import SeatRead
from app.services.tmdb import get_trailer_key
from app.services.discovery import VIETNAM_TIMEZONE, utc_now, vietnamese_date_range
from app.services.collector_monitoring import get_collector_freshness

router = APIRouter(tags=["pages"])


@router.get("/")
async def home_page(
    request: Request,
    title: str | None = None,
    source: str | None = None,
    city: str | None = None,
    show_date: date | None = Query(default=None, alias="date"),
    page: int = Query(default=1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    selected_date = show_date or utc_now().astimezone(VIETNAM_TIMEZONE).date()
    start, end = vietnamese_date_range(selected_date)
    showtime_filter = [
        Showtime.movie_id == Movie.id,
        Showtime.start_time >= start,
        Showtime.start_time < end,
    ]
    if selected_date == utc_now().astimezone(VIETNAM_TIMEZONE).date():
        showtime_filter.append(Showtime.start_time >= utc_now())

    availability_query = select(Showtime.id).where(*showtime_filter)
    if city:
        availability_query = availability_query.join(
            Cinema, Showtime.cinema_id == Cinema.id
        ).where(Cinema.city.ilike(f"%{city.strip()}%"))
    if source:
        availability_query = availability_query.where(Showtime.source == source)
    query = select(Movie).where(availability_query.exists())
    if title:
        query = query.where(Movie.title.ilike(f"%{title.strip()}%"))
    page_size = 12
    result = await db.execute(
        query.order_by(Movie.title, Movie.id)
        .offset((page - 1) * page_size)
        .limit(page_size + 1)
    )
    page_items = list(result.scalars().all())
    has_next = len(page_items) > page_size
    movies = page_items[:page_size]
    movie_sources: dict[int, list[str]] = {movie.id: [] for movie in movies}
    if movies:
        sources_query = select(Showtime.movie_id, Showtime.source).where(
            Showtime.movie_id.in_(movie_sources),
            Showtime.start_time >= start,
            Showtime.start_time < end,
        )
        if selected_date == utc_now().astimezone(VIETNAM_TIMEZONE).date():
            sources_query = sources_query.where(Showtime.start_time >= utc_now())
        if city:
            sources_query = sources_query.join(
                Cinema, Showtime.cinema_id == Cinema.id
            ).where(Cinema.city.ilike(f"%{city.strip()}%"))
        if source:
            sources_query = sources_query.where(Showtime.source == source)
        for movie_id, provider_source in (await db.execute(sources_query)).all():
            if provider_source and provider_source not in movie_sources[movie_id]:
                movie_sources[movie_id].append(provider_source)
    collector_freshness = await get_collector_freshness(db)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "movies": movies,
            "movie_sources": movie_sources,
            "title_query": title or "",
            "selected_source": source or "",
            "city_query": city or "",
            "selected_date": selected_date,
            "page": page,
            "has_next": has_next,
            "collector_freshness": collector_freshness,
        },
    )


@router.get("/nearby-cinemas")
async def nearby_cinemas_page(request: Request):
    return templates.TemplateResponse(request=request, name="nearby_cinemas.html")


@router.get("/cinema/{cinema_id}")
async def cinema_showtimes_page(
    cinema_id: int,
    request: Request,
    show_date: date | None = Query(default=None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    cinema = await db.get(Cinema, cinema_id)
    if cinema is None:
        return templates.TemplateResponse(
            request=request, name="404.html", status_code=404
        )

    today = utc_now().astimezone(VIETNAM_TIMEZONE).date()
    selected_date = show_date or today
    start, end = vietnamese_date_range(selected_date)
    query = (
        select(Showtime, CinemaRoom, Movie)
        .outerjoin(CinemaRoom, Showtime.room_id == CinemaRoom.id)
        .join(Movie, Showtime.movie_id == Movie.id)
        .where(
            Showtime.cinema_id == cinema_id,
            Showtime.start_time >= start,
            Showtime.start_time < end,
        )
        .order_by(Movie.title, Showtime.start_time)
    )
    if selected_date == today:
        query = query.where(Showtime.start_time >= utc_now())

    grouped = {}
    for showtime, room, movie in (await db.execute(query)).all():
        group = grouped.setdefault(
            movie.id,
            {"movie": movie, "showtimes": []},
        )
        group["showtimes"].append({"showtime": showtime, "room": room})

    return templates.TemplateResponse(
        request=request,
        name="cinema_showtimes.html",
        context={
            "cinema": cinema,
            "movie_groups": list(grouped.values()),
            "selected_date": selected_date,
            "date_options": [today + timedelta(days=offset) for offset in range(7)],
        },
    )


@router.get("/movie/{movie_id}")
async def movie_detail_page(
    movie_id: int,
    request: Request,
    show_date: date | None = Query(default=None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    movie = await db.get(Movie, movie_id)
    if movie is None:
        return templates.TemplateResponse(
            request=request, name="404.html", status_code=404
        )

    today = utc_now().astimezone(VIETNAM_TIMEZONE).date()
    selected_date = show_date or today
    start, end = vietnamese_date_range(selected_date)
    query = (
        select(Showtime, CinemaRoom, Cinema)
        .outerjoin(CinemaRoom, Showtime.room_id == CinemaRoom.id)
        .join(Cinema, Showtime.cinema_id == Cinema.id)
        .where(
            Showtime.movie_id == movie_id,
            Showtime.start_time >= start,
            Showtime.start_time < end,
        )
        .order_by(Cinema.name, Showtime.start_time)
    )
    if selected_date == today:
        query = query.where(Showtime.start_time >= utc_now())

    grouped = {}
    for showtime, room, cinema in (await db.execute(query)).all():
        group = grouped.setdefault(
            cinema.id,
            {
                "cinema": cinema,
                "showtimes": [],
            },
        )
        group["showtimes"].append({"showtime": showtime, "room": room})

    # Chỉ phim được import từ TMDB (có tmdb_id) mới tra được trailer.
    # Phim nhập tay qua POST /movies sẽ không có trailer -> trailer_key=None,
    # template tự ẩn phần trailer nếu không có (xem movie_detail.html).
    trailer_key = None
    if movie.tmdb_id is not None:
        trailer_key = await get_trailer_key(movie.tmdb_id)

    return templates.TemplateResponse(
        request=request,
        name="movie_detail.html",
        context={
            "movie": movie,
            "showtime_groups": list(grouped.values()),
            "trailer_key": trailer_key,
            "selected_date": selected_date,
            "date_options": [today + timedelta(days=offset) for offset in range(7)],
        },
    )


@router.get("/showtime/{showtime_id}/seats")
async def seat_selection_page(
    showtime_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    query = (
        select(Showtime)
        .where(Showtime.id == showtime_id)
        .options(selectinload(Showtime.movie))
    )
    result = await db.execute(query)
    showtime = result.scalar_one_or_none()

    if showtime is None:
        return templates.TemplateResponse(
            request=request, name="404.html", status_code=404
        )
    if showtime.booking_mode == "external_redirect":
        return RedirectResponse(showtime.external_booking_url or f"/movie/{showtime.movie_id}")

    inventory_result = await db.execute(
        select(Seat, ShowtimeSeat.status)
        .join(ShowtimeSeat, ShowtimeSeat.seat_id == Seat.id)
        .where(ShowtimeSeat.showtime_id == showtime_id)
        .order_by(Seat.row_label, Seat.col_number)
    )
    inventory_rows = inventory_result.all()
    hold_keys = [
        f"seat_hold:{showtime_id}:{seat.id}" for seat, _status in inventory_rows
    ]
    holders = await redis_client.mget(hold_keys) if hold_keys else []
    seats_by_row: dict[str, list[SeatRead]] = {}
    for (seat, inventory_status), holder in zip(inventory_rows, holders):
        seat_status = inventory_status.value
        if seat_status == "available" and holder is not None:
            seat_status = "held"
        seat_read = SeatRead(
            id=seat.id,
            seat_label=seat.seat_label,
            row_label=seat.row_label,
            col_number=seat.col_number,
            status=seat_status,
        )
        seats_by_row.setdefault(seat.row_label, []).append(seat_read)

    return templates.TemplateResponse(
        request=request,
        name="seats.html",
        context={"showtime": showtime, "seats_by_row": seats_by_row},
    )


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@router.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse(request=request, name="register.html")


@router.get("/my-bookings")
async def my_bookings_page(request: Request):
    return templates.TemplateResponse(request=request, name="my_bookings.html")

@router.get("/forgot-password")
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request=request, name="forgot_password.html")


@router.get("/reset-password")
async def reset_password_page(request: Request, token: str = ""):
    return templates.TemplateResponse(
        request=request, name="reset_password.html", context={"token": token}
    )
