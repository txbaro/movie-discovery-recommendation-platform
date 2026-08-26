import string
from datetime import date, datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import CinemaCollector
from app.collectors.schemas import CollectedShowtime
from app.models.booking import Booking
from app.models.cinema import Cinema
from app.models.cinema_room import CinemaRoom
from app.models.movie import Movie, clean_movie_title, normalize_movie_title
from app.models.provider_movie import ProviderMovie
from app.models.seat import Seat
from app.models.showtime import Showtime
from app.models.showtime_seat import ShowtimeSeat


class SyncResult(BaseModel):
    collected: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)


async def _find_external(db: AsyncSession, model, source: str, external_id: str):
    return await db.scalar(
        select(model).where(model.source == source, model.external_id == external_id)
    )


SOURCE_PRIORITY = {
    "tmdb": 100,
    "galaxy": 40,
    "lotte": 30,
    "cinestar": 20,
    "fixture": 10,
}
GENRE_PLACEHOLDERS = {"chưa phân loại", "chua phan loai", "unknown"}
DESCRIPTION_PLACEHOLDERS = {
    "chưa có mô tả",
    "thông tin phim từ galaxy cinema.",
    "thông tin phim từ lotte cinema.",
}


def _is_meaningful(value: str | None, placeholders: set[str]) -> bool:
    return bool(value and value.strip().lower() not in placeholders)


def _provider_values(item: CollectedShowtime) -> dict:
    return {
        "title": item.movie.title,
        "genres": item.movie.genres,
        "description": item.movie.description,
        "duration_minutes": item.movie.duration_minutes,
        "rating": item.movie.rating,
        "poster_url": item.movie.poster_url,
    }


def _merge_canonical_movie(
    movie: Movie,
    source: str,
    item: CollectedShowtime,
) -> bool:
    changed = False
    current_priority = SOURCE_PRIORITY.get(movie.metadata_source or "", 0)
    incoming_priority = SOURCE_PRIORITY.get(source, 1)
    if incoming_priority > current_priority:
        changed |= _apply_values(
            movie,
            {
                "title": clean_movie_title(item.movie.title),
                "duration_minutes": item.movie.duration_minutes,
                "metadata_source": source,
            },
        )
    if item.movie.poster_url and (
        not movie.poster_url or incoming_priority > current_priority
    ):
        changed |= _apply_values(movie, {"poster_url": item.movie.poster_url})
    if _is_meaningful(item.movie.genres, GENRE_PLACEHOLDERS) and (
        not _is_meaningful(movie.genres, GENRE_PLACEHOLDERS)
        or incoming_priority > current_priority
    ):
        changed |= _apply_values(movie, {"genres": item.movie.genres})
    if _is_meaningful(item.movie.description, DESCRIPTION_PLACEHOLDERS) and (
        not _is_meaningful(movie.description, DESCRIPTION_PLACEHOLDERS)
        or incoming_priority > current_priority
    ):
        changed |= _apply_values(movie, {"description": item.movie.description})
    return changed


async def _resolve_provider_movie(
    db: AsyncSession,
    source: str,
    item: CollectedShowtime,
    now: datetime,
) -> tuple[Movie, ProviderMovie, bool]:
    provider_movie = await _find_external(
        db, ProviderMovie, source, item.movie.external_id
    )
    provider_values = _provider_values(item)
    if provider_movie is not None:
        movie = await db.get(Movie, provider_movie.movie_id)
        if movie is None:
            raise ValueError("provider movie tham chiếu canonical movie không tồn tại")
        changed = _apply_values(provider_movie, provider_values)
        provider_movie.last_synced_at = now
        changed |= _merge_canonical_movie(movie, source, item)
        return movie, provider_movie, changed

    normalized_title = normalize_movie_title(item.movie.title)
    duration = item.movie.duration_minutes
    movie = await db.scalar(
        select(Movie)
        .where(
            Movie.normalized_title == normalized_title,
            Movie.duration_minutes.between(duration - 10, duration + 10),
        )
        .order_by(func.abs(Movie.duration_minutes - duration), Movie.id)
    )
    if movie is None:
        movie = Movie(
            title=clean_movie_title(item.movie.title),
            genres=item.movie.genres,
            description=item.movie.description,
            duration_minutes=duration,
            rating=None,
            poster_url=item.movie.poster_url,
            metadata_source=source,
        )
        db.add(movie)
        await db.flush()
    else:
        _merge_canonical_movie(movie, source, item)

    provider_movie = ProviderMovie(
        movie_id=movie.id,
        source=source,
        external_id=item.movie.external_id,
        last_synced_at=now,
        **provider_values,
    )
    db.add(provider_movie)
    await db.flush()
    return movie, provider_movie, True


def _apply_values(obj, values: dict) -> bool:
    changed = False
    for field, value in values.items():
        if getattr(obj, field) != value:
            setattr(obj, field, value)
            changed = True
    return changed


async def _ensure_room_seats(
    db: AsyncSession,
    room: CinemaRoom,
    rows: int,
    cols: int,
) -> list[Seat]:
    seats = (
        await db.execute(
            select(Seat)
            .where(Seat.room_id == room.id)
            .order_by(Seat.row_label, Seat.col_number)
        )
    ).scalars().all()
    if seats:
        return list(seats)

    seats = [
        Seat(
            room_id=room.id,
            showtime_id=None,
            seat_label=f"{string.ascii_uppercase[row]}{column}",
            row_label=string.ascii_uppercase[row],
            col_number=column,
        )
        for row in range(rows)
        for column in range(1, cols + 1)
    ]
    db.add_all(seats)
    await db.flush()
    return seats


async def _sync_one(
    db: AsyncSession,
    source: str,
    item: CollectedShowtime,
) -> str:
    now = datetime.now(timezone.utc)
    changed = False

    cinema = await _find_external(db, Cinema, source, item.cinema.external_id)
    if cinema is None:
        cinema = Cinema(
            source=source,
            external_id=item.cinema.external_id,
            name=item.cinema.name,
            address=item.cinema.address,
            city=item.cinema.city,
            latitude=item.cinema.latitude,
            longitude=item.cinema.longitude,
            last_synced_at=now,
        )
        db.add(cinema)
        await db.flush()
    else:
        changed |= _apply_values(
            cinema,
            {
                "name": item.cinema.name,
                "address": item.cinema.address,
                "city": item.cinema.city,
                "latitude": item.cinema.latitude,
                "longitude": item.cinema.longitude,
            },
        )
        cinema.last_synced_at = now

    room = None
    seats: list[Seat] = []
    if item.room is not None:
        room = await _find_external(db, CinemaRoom, source, item.room.external_id)
        if room is None:
            room = CinemaRoom(
                cinema_id=cinema.id,
                source=source,
                external_id=item.room.external_id,
                name=item.room.name,
                last_synced_at=now,
            )
            db.add(room)
            await db.flush()
        else:
            changed |= _apply_values(
                room, {"cinema_id": cinema.id, "name": item.room.name}
            )
            room.last_synced_at = now
        seats = await _ensure_room_seats(db, room, item.room.rows, item.room.cols)

    movie, provider_movie, movie_changed = await _resolve_provider_movie(
        db, source, item, now
    )
    changed |= movie_changed

    showtime = await _find_external(db, Showtime, source, item.external_id)
    if showtime is None:
        showtime = Showtime(
            source=source,
            external_id=item.external_id,
            movie_id=movie.id,
            provider_movie_id=provider_movie.id,
            cinema_id=cinema.id,
            room_id=room.id if room else None,
            start_time=item.start_time,
            price=item.price,
            room_rows=item.room.rows if item.room else 0,
            room_cols=item.room.cols if item.room else 0,
            booking_mode=item.booking_mode,
            external_booking_url=item.external_booking_url,
            format=item.format,
            language=item.language,
            last_synced_at=now,
        )
        db.add(showtime)
        await db.flush()
        if item.booking_mode == "internal":
            db.add_all(
                ShowtimeSeat(showtime_id=showtime.id, seat_id=seat.id)
                for seat in seats
            )
        return "created"

    showtime_values = {
        "movie_id": movie.id,
        "provider_movie_id": provider_movie.id,
        "cinema_id": cinema.id,
        "room_id": room.id if room else None,
        "start_time": item.start_time,
        "price": item.price,
        "room_rows": item.room.rows if item.room else 0,
        "room_cols": item.room.cols if item.room else 0,
        "booking_mode": item.booking_mode,
        "external_booking_url": item.external_booking_url,
        "format": item.format,
        "language": item.language,
    }
    room_changed = showtime.room_id != (room.id if room else None)
    mode_changed = showtime.booking_mode != item.booking_mode
    if room_changed or mode_changed:
        booking_count = await db.scalar(
            select(func.count(Booking.id)).where(Booking.showtime_id == showtime.id)
        )
        if booking_count:
            raise ValueError("không thể đổi phòng/booking mode của suất đã có booking")
    changed |= _apply_values(showtime, showtime_values)
    showtime.last_synced_at = now

    if item.booking_mode == "external_redirect":
        await db.execute(
            delete(ShowtimeSeat).where(ShowtimeSeat.showtime_id == showtime.id)
        )
        changed |= room_changed or mode_changed
    elif room_changed or mode_changed:
        await db.execute(
            delete(ShowtimeSeat).where(ShowtimeSeat.showtime_id == showtime.id)
        )
        db.add_all(
            ShowtimeSeat(showtime_id=showtime.id, seat_id=seat.id) for seat in seats
        )
    else:
        existing_seat_ids = set(
            (
                await db.execute(
                    select(ShowtimeSeat.seat_id).where(
                        ShowtimeSeat.showtime_id == showtime.id
                    )
                )
            ).scalars()
        )
        missing = [seat for seat in seats if seat.id not in existing_seat_ids]
        db.add_all(
            ShowtimeSeat(showtime_id=showtime.id, seat_id=seat.id) for seat in missing
        )
        changed |= bool(missing)

    return "updated" if changed else "skipped"


async def sync_collector(
    db: AsyncSession,
    collector: CinemaCollector,
    target_date: date,
) -> SyncResult:
    items = await collector.collect(target_date)
    return await sync_collected_showtimes(db, collector.source, items)


async def sync_collected_showtimes(
    db: AsyncSession,
    source: str,
    items: list[CollectedShowtime],
) -> SyncResult:
    """Đồng bộ một batch đã collect; dùng cho collector hỗ trợ date range."""
    result = SyncResult(collected=len(items))

    for item in items:
        try:
            async with db.begin_nested():
                outcome = await _sync_one(db, source, item)
            setattr(result, outcome, getattr(result, outcome) + 1)
        except Exception as exc:
            result.failed += 1
            result.errors.append(f"{item.external_id}: {exc}")

    await db.commit()
    return result
