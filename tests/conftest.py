from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.redis_client import redis_client
from app.main import app
from app.models.cinema import Cinema
from app.models.cinema_room import CinemaRoom
from app.models.movie import Movie
from app.models.seat import Seat
from app.models.showtime import BookingMode, Showtime
from app.models.showtime_seat import ShowtimeSeat
from app.services.discovery import utc_now


@pytest_asyncio.fixture(autouse=True)
async def clean_state() -> AsyncIterator[None]:
    """Mỗi test bắt đầu với PostgreSQL và Redis hoàn toàn sạch."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "TRUNCATE collector_runs, user_events, movie_embeddings, "
                "booking_seats, bookings, "
                "showtime_seats, seats, "
                "showtimes, cinema_rooms, cinemas, movies, users "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    await redis_client.flushdb()
    yield
    await redis_client.flushdb()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest_asyncio.fixture
async def catalogue() -> dict[str, int]:
    """Một phim, rạp, phòng, suất chiếu tương lai và hai ghế đặt được."""
    async with AsyncSessionLocal() as session:
        movie = Movie(
            title="Step 9 Test Movie",
            genres="Action",
            description="Integration-test fixture",
            duration_minutes=120,
            rating=8.5,
        )
        cinema = Cinema(
            name="Test Cinema",
            address="1 Test Street",
            city="Hồ Chí Minh",
            source="fixture",
            external_id="test-cinema",
            latitude=Decimal("10.776900"),
            longitude=Decimal("106.700900"),
        )
        session.add_all([movie, cinema])
        await session.flush()
        room = CinemaRoom(cinema_id=cinema.id, name="Room 1")
        session.add(room)
        await session.flush()
        seats = [
            Seat(room_id=room.id, seat_label="A1", row_label="A", col_number=1),
            Seat(room_id=room.id, seat_label="A2", row_label="A", col_number=2),
        ]
        session.add_all(seats)
        await session.flush()
        showtime = Showtime(
            movie_id=movie.id,
            cinema_id=cinema.id,
            room_id=room.id,
            start_time=utc_now() + timedelta(hours=1),
            room_rows=1,
            room_cols=2,
            price=Decimal("90000"),
            booking_mode=BookingMode.INTERNAL.value,
            source="fixture",
            external_id="test-showtime",
        )
        session.add(showtime)
        await session.flush()
        session.add_all(
            [ShowtimeSeat(showtime_id=showtime.id, seat_id=seat.id) for seat in seats]
        )
        await session.commit()
        return {
            "movie_id": movie.id,
            "cinema_id": cinema.id,
            "room_id": room.id,
            "showtime_id": showtime.id,
            "seat_1_id": seats[0].id,
            "seat_2_id": seats[1].id,
        }


async def register_and_login(client: AsyncClient, email: str) -> int:
    register = await client.post(
        "/auth/register",
        json={"email": email, "full_name": "Test User", "password": "password123"},
    )
    assert register.status_code == 201, register.text
    login = await client.post(
        "/auth/login", json={"email": email, "password": "password123"}
    )
    assert login.status_code == 200, login.text
    return register.json()["id"]
