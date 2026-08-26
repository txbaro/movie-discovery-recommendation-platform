from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.config import settings
from app.core.dependencies import get_current_user
from app.core.i18n import get_locale, translate, translate_genres
from app.models.booking import Booking
from app.models.cinema import Cinema
from app.models.movie import Movie, normalize_movie_title
from app.models.showtime import Showtime
from app.models.user import User
from app.models.user_event import UserEvent
from app.schemas.recommendation import (
    NaturalLanguageRecommendationRequest,
    NaturalLanguageRecommendationResponse,
    NearestShowtime,
    RecommendedMovie,
    SemanticRecommendedMovie,
)
from app.services.discovery import (
    VIETNAM_TIMEZONE,
    distance_km,
    utc_now,
    vietnamese_date_range,
)
from app.services.recommendation import (
    EVENT_WEIGHTS,
    get_recommendations_for_user,
    get_similar_movies,
)
from app.services.redis_features import QuotaResult, consume_ai_quota
from app.services.semantic_recommendation import get_semantic_scores
from app.services.prompt_constraints import (
    movie_matches_any_genre,
    parse_prompt_constraints,
)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


async def _build_user_movie_weights(
    db: AsyncSession,
    user_id: int,
) -> dict[int, float]:
    now = utc_now()
    cutoff = now - timedelta(days=180)
    movie_weights: dict[int, float] = {}

    events = list(
        (
            await db.scalars(
                select(UserEvent)
                .where(
                    UserEvent.user_id == user_id,
                    UserEvent.occurred_at >= cutoff,
                )
                .order_by(UserEvent.occurred_at.desc())
            )
        ).all()
    )
    movies = list((await db.scalars(select(Movie))).all())
    for event in events:
        age_days = max(0.0, (now - event.occurred_at).total_seconds() / 86400)
        decay = 0.5 ** (age_days / 30)
        base_weight = EVENT_WEIGHTS.get(event.event_type, 0.0)
        if event.movie_id is not None:
            movie_weights[event.movie_id] = (
                movie_weights.get(event.movie_id, 0.0) + base_weight * decay
            )
        elif event.event_type == "movie_searched" and event.search_query:
            normalized_query = normalize_movie_title(event.search_query)
            if len(normalized_query) < 3:
                continue
            matches = [
                movie
                for movie in movies
                if normalized_query in movie.normalized_title
            ][:5]
            for movie in matches:
                movie_weights[movie.id] = (
                    movie_weights.get(movie.id, 0.0) + base_weight * decay
                )

    booking_rows = (
        await db.execute(
            select(Showtime.movie_id, Booking.booked_at)
            .join(Booking, Booking.showtime_id == Showtime.id)
            .where(Booking.user_id == user_id)
        )
    ).all()
    for movie_id, booked_at in booking_rows:
        age_days = max(0.0, (now - booked_at).total_seconds() / 86400)
        movie_weights[movie_id] = movie_weights.get(movie_id, 0.0) + (
            EVENT_WEIGHTS["internal_booking_confirmed"]
            * (0.5 ** (age_days / 30))
        )
    return movie_weights


@router.get("/movie/{movie_id}", response_model=list[RecommendedMovie])
async def similar_movies(
    movie_id: int, limit: int = 5, db: AsyncSession = Depends(get_db)
):
    """
    Gợi ý phim tương tự 1 phim cụ thể — dùng cho trang chi tiết phim,
    kiểu "Nếu bạn thích phim này, có thể bạn cũng thích...".
    Không cần đăng nhập vì không phụ thuộc lịch sử cá nhân.
    """
    movie = await db.get(Movie, movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phim")

    results = await get_similar_movies(db, movie_id, top_n=limit)
    return [
        RecommendedMovie(movie=m, similarity_score=round(score, 3))
        for m, score in results
    ]


@router.get("/me", response_model=list[RecommendedMovie])
async def recommendations_for_me(
    limit: int = 5,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Gợi ý dựa trên hành vi discovery gần đây; booking nội bộ là tín hiệu phụ.
    """
    movie_weights = await _build_user_movie_weights(db, current_user.id)
    results = await get_recommendations_for_user(db, movie_weights, top_n=limit)
    return [
        RecommendedMovie(movie=m, similarity_score=round(score, 3))
        for m, score in results
    ]


@router.post(
    "/natural-language",
    response_model=NaturalLanguageRecommendationResponse,
)
async def natural_language_recommendations(
    payload: NaturalLanguageRecommendationRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Hybrid retrieval: semantic prompt + behavior + popularity + rating."""
    prompt = payload.prompt.strip()
    locale = get_locale(request)
    constraints = parse_prompt_constraints(prompt)
    now = utc_now()
    today = now.astimezone(VIETNAM_TIMEZONE).date()
    if payload.date is not None and payload.date < today:
        raise HTTPException(status_code=422, detail="Ngày xem không thể ở quá khứ")

    quota: QuotaResult | None = None
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",", 1)[0]
    client_ip = forwarded_for.strip() or (
        request.client.host if request.client else "unknown"
    )
    try:
        quota = await consume_ai_quota(current_user.id, client_ip)
    except RedisError as exc:
        if settings.GEMINI_API_KEY:
            raise HTTPException(
                status_code=503,
                detail="Không thể kiểm tra quota AI, vui lòng thử lại",
            ) from exc
    if quota is not None and not quota.allowed:
        raise HTTPException(
            status_code=429,
            detail="Đã hết lượt gợi ý hôm nay",
            headers={"Retry-After": str(quota.reset_seconds)},
        )
    conditions = []
    if payload.date is not None:
        start, end = vietnamese_date_range(payload.date)
        conditions.extend([Showtime.start_time >= start, Showtime.start_time < end])
        if payload.date == today:
            conditions.append(Showtime.start_time >= now)
    else:
        conditions.append(Showtime.start_time >= now)

    query = (
        select(Showtime, Movie, Cinema)
        .join(Movie, Showtime.movie_id == Movie.id)
        .join(Cinema, Showtime.cinema_id == Cinema.id)
        .where(*conditions)
        .order_by(Showtime.start_time)
    )
    if payload.city and payload.city.strip() and payload.latitude is None:
        query = query.where(
            Cinema.city.ilike(f"%{payload.city.strip()}%")
        )
    showtime_rows = (await db.execute(query.limit(5000))).all()
    candidates: dict[int, dict] = {}
    for showtime, movie, cinema in showtime_rows:
        if movie_matches_any_genre(movie.genres, constraints.excluded_genres):
            continue
        cinema_distance = None
        if payload.latitude is not None and payload.longitude is not None:
            if cinema.latitude is None or cinema.longitude is None:
                continue
            cinema_distance = distance_km(
                payload.latitude,
                payload.longitude,
                float(cinema.latitude),
                float(cinema.longitude),
            )
            if cinema_distance > payload.radius_km:
                continue
        candidate = candidates.setdefault(
            movie.id,
            {"movie": movie, "showtimes": []},
        )
        candidate["showtimes"].append((showtime, cinema, cinema_distance))

    movies = [candidate["movie"] for candidate in candidates.values()]
    showtime_counts = {
        movie_id: len(candidate["showtimes"])
        for movie_id, candidate in candidates.items()
    }

    semantic_scores, engine = await get_semantic_scores(db, movies, prompt)
    movie_weights = await _build_user_movie_weights(db, current_user.id)
    behavior_scores: dict[int, float] = {}
    if movie_weights:
        behavior_results = await get_recommendations_for_user(
            db, movie_weights, top_n=max(1, len(movies))
        )
        behavior_scores = {movie.id: score for movie, score in behavior_results}

    max_showtimes = max(showtime_counts.values(), default=1)
    ranked = []
    for movie in movies:
        movie_showtimes = candidates[movie.id]["showtimes"]
        if payload.latitude is not None:
            nearest_showtime, nearest_cinema, nearest_distance = min(
                movie_showtimes,
                key=lambda item: (item[2], item[0].start_time),
            )
        else:
            nearest_showtime, nearest_cinema, nearest_distance = min(
                movie_showtimes,
                key=lambda item: item[0].start_time,
            )
        semantic_score = semantic_scores.get(movie.id, 0.0)
        if movie_matches_any_genre(movie.genres, constraints.included_genres):
            semantic_score = min(1.0, semantic_score + 0.10)
        if movie_matches_any_genre(movie.genres, constraints.soft_avoid_genres):
            semantic_score = max(0.0, semantic_score - 0.20)
        behavior_score = behavior_scores.get(movie.id, 0.0)
        popularity_score = showtime_counts[movie.id] / max_showtimes
        rating_score = (
            max(0.0, min(1.0, (movie.rating or 0.0) / 10))
            if movie.rating_source == "tmdb"
            else 0.0
        )
        proximity_score = None
        if nearest_distance is not None:
            proximity_score = max(0.0, 1 - nearest_distance / payload.radius_km)
            final_score = (
                semantic_score * 0.55
                + behavior_score * 0.20
                + proximity_score * 0.15
                + popularity_score * 0.05
                + rating_score * 0.05
            )
        else:
            final_score = (
                semantic_score * 0.60
                + behavior_score * 0.25
                + popularity_score * 0.10
                + rating_score * 0.05
            )
        reason_parts = [
            translate(
                locale,
                "semantic.reason_match",
                percent=round(semantic_score * 100),
            ),
            translate(
                locale,
                "semantic.reason_genre",
                genres=movie.genres or translate(locale, "common.unknown_genre"),
            ),
            translate(
                locale,
                "semantic.reason_showtimes",
                count=showtime_counts[movie.id],
            ),
        ]
        if nearest_distance is not None:
            reason_parts.append(
                translate(
                    locale,
                    "semantic.reason_nearest",
                    cinema=nearest_cinema.name,
                    distance=f"{nearest_distance:.1f}",
                )
            )
        if behavior_score > 0:
            reason_parts.append(translate(locale, "semantic.reason_behavior"))
        ranked.append(
            (
                final_score,
                SemanticRecommendedMovie(
                    movie=movie,
                    semantic_score=round(semantic_score, 3),
                    behavior_score=round(behavior_score, 3),
                    proximity_score=(
                        round(proximity_score, 3)
                        if proximity_score is not None
                        else None
                    ),
                    final_score=round(final_score, 3),
                    showtime_count=showtime_counts[movie.id],
                    nearest_showtime=NearestShowtime(
                        id=nearest_showtime.id,
                        cinema_id=nearest_cinema.id,
                        cinema_name=nearest_cinema.name,
                        cinema_address=nearest_cinema.address,
                        city=nearest_cinema.city,
                        start_time=nearest_showtime.start_time,
                        distance_km=(
                            round(nearest_distance, 2)
                            if nearest_distance is not None
                            else None
                        ),
                        booking_mode=nearest_showtime.booking_mode,
                        booking_url=(
                            nearest_showtime.external_booking_url
                            or f"/showtime/{nearest_showtime.id}/seats"
                        ),
                        source=nearest_showtime.source,
                        format=nearest_showtime.format,
                        language=nearest_showtime.language,
                    ),
                    reason="; ".join(reason_parts) + ".",
                ),
            )
        )
    ranked.sort(key=lambda item: item[0], reverse=True)
    results = [item for _score, item in ranked[: payload.limit]]

    context_id = str(uuid4())
    db.add(
        UserEvent(
            user_id=current_user.id,
            event_type="preference_prompt_submitted",
            search_query=prompt,
            context_id=context_id,
            source="semantic_recommendation",
            event_data={
                "engine": engine,
                "location_used": payload.latitude is not None,
                "radius_km": payload.radius_km if payload.latitude is not None else None,
                "recommended_movie_ids": [item.movie.id for item in results],
                "prompt_constraints": {
                    "included_genres": list(constraints.included_genres),
                    "excluded_genres": list(constraints.excluded_genres),
                    "soft_avoid_genres": list(constraints.soft_avoid_genres),
                },
                "scores": {
                    str(item.movie.id): item.final_score for item in results
                },
            },
        )
    )
    await db.commit()
    return NaturalLanguageRecommendationResponse(
        context_id=context_id,
        engine=engine,
        included_genres=translate_genres(
            locale, constraints.included_genres
        ),
        excluded_genres=translate_genres(
            locale, constraints.excluded_genres
        ),
        soft_avoid_genres=translate_genres(
            locale, constraints.soft_avoid_genres
        ),
        quota_remaining=quota.remaining if quota is not None else None,
        quota_reset_seconds=quota.reset_seconds if quota is not None else None,
        results=results,
    )
