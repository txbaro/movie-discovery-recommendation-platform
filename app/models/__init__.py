"""
Import tất cả model ở đây để:
1. Alembic autogenerate có thể quét được toàn bộ schema khi tạo migration
2. Các model tham chiếu chéo nhau (vd Movie <-> Showtime) được SQLAlchemy
   nhận diện đầy đủ, tránh lỗi "relationship not found"
"""
from app.models.user import User
from app.models.user_event import EventType, UserEvent
from app.models.movie import Movie
from app.models.movie_embedding import MovieEmbedding
from app.models.provider_movie import ProviderMovie
from app.models.showtime import Showtime
from app.models.seat import Seat, SeatStatus
from app.models.booking import Booking
from app.models.cinema import Cinema
from app.models.cinema_room import CinemaRoom
from app.models.showtime_seat import ShowtimeSeat
from app.models.booking_seat import BookingSeat
from app.models.collector_run import CollectorRun, CollectorRunStatus

__all__ = [
    "User",
    "UserEvent",
    "EventType",
    "Movie",
    "MovieEmbedding",
    "ProviderMovie",
    "Showtime",
    "Seat",
    "SeatStatus",
    "Booking",
    "Cinema",
    "CinemaRoom",
    "ShowtimeSeat",
    "BookingSeat",
    "CollectorRun",
    "CollectorRunStatus",
]
