import re
import unicodedata

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.database import Base


RATING_SUFFIX_PATTERN = re.compile(
    r"(?:\s+(?:lt|rerun))?\s*\((?:t\d+|c\d+|p|k)\)\s*$",
    re.IGNORECASE,
)
TRAILING_VARIANT_PATTERN = re.compile(r"\s+(?:lt|rerun)\s*$", re.IGNORECASE)


def clean_movie_title(title: str) -> str:
    result = RATING_SUFFIX_PATTERN.sub("", title.strip())
    result = TRAILING_VARIANT_PATTERN.sub("", result)
    return result.strip(" -–—:")


def normalize_movie_title(title: str) -> str:
    cleaned = clean_movie_title(title).replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFKD", cleaned)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return "".join(
        character for character in without_accents.lower() if character.isalnum()
    )


class Movie(Base):
    __tablename__ = "movies"
    __table_args__ = (Index("ix_movies_normalized_title", "normalized_title"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    normalized_title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Thể loại lưu dạng "Action,Sci-Fi,Thriller" — đơn giản cho MVP.
    # Nếu sau này cần query phức tạp hơn, có thể tách thành bảng riêng (many-to-many).
    genres: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    # Canonical rating only comes from TMDB.  NULL means TMDB has not matched
    # the movie, or the matched movie does not have any votes yet.
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating_vote_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    poster_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    metadata_source: Mapped[str | None] = mapped_column(String(50), nullable=True)

    showtimes: Mapped[list["Showtime"]] = relationship(back_populates="movie")
    provider_movies: Mapped[list["ProviderMovie"]] = relationship(
        back_populates="movie", cascade="all, delete-orphan", passive_deletes=True
    )
    embedding: Mapped["MovieEmbedding | None"] = relationship(
        back_populates="movie",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    @validates("title")
    def normalize_title(self, _key: str, value: str) -> str:
        cleaned = clean_movie_title(value)
        self.normalized_title = normalize_movie_title(cleaned)
        return cleaned
