from datetime import date as Date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.movie import MovieRead


class RecommendedMovie(BaseModel):
    movie: MovieRead
    similarity_score: float


class NaturalLanguageRecommendationRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=1000)
    city: str | None = Field(default=None, max_length=100)
    date: Date | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float = Field(default=20, gt=0, le=100)
    limit: int = Field(default=5, ge=1, le=10)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 10:
            raise ValueError("Mô tả cần có ít nhất 10 ký tự")
        return value

    @model_validator(mode="after")
    def validate_coordinates(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude và longitude phải được gửi cùng nhau")
        return self


class NearestShowtime(BaseModel):
    id: int
    cinema_id: int
    cinema_name: str
    cinema_address: str
    city: str
    start_time: datetime
    distance_km: float | None
    booking_mode: str
    booking_url: str
    source: str | None
    format: str | None
    language: str | None


class SemanticRecommendedMovie(BaseModel):
    movie: MovieRead
    semantic_score: float
    behavior_score: float
    proximity_score: float | None
    final_score: float
    showtime_count: int
    nearest_showtime: NearestShowtime
    reason: str


class NaturalLanguageRecommendationResponse(BaseModel):
    context_id: str
    engine: str
    included_genres: list[str] = Field(default_factory=list)
    excluded_genres: list[str] = Field(default_factory=list)
    soft_avoid_genres: list[str] = Field(default_factory=list)
    quota_remaining: int | None
    quota_reset_seconds: int | None
    results: list[SemanticRecommendedMovie]
