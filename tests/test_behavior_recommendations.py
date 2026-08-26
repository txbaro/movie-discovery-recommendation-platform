from datetime import timedelta
import json

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.movie import Movie
from app.models.showtime import BookingMode, Showtime
from app.services import semantic_recommendation
from app.services.discovery import utc_now
from app.services.redis_features import distributed_lock
from tests.conftest import register_and_login


async def _add_recommendation_candidates(catalogue: dict[str, int]) -> tuple[int, int, int]:
    async with AsyncSessionLocal() as db:
        action = Movie(
            title="Action Candidate",
            genres="Action",
            description="Explosive action heroes and adventure",
            duration_minutes=118,
            rating=8.0,
        )
        romance = Movie(
            title="Romance Candidate",
            genres="Romance",
            description="A quiet emotional love story",
            duration_minutes=110,
            rating=7.0,
        )
        external = Movie(
            title="External Intent Movie",
            genres="Animation",
            description="Animated family adventure",
            duration_minutes=100,
            rating=8.2,
        )
        db.add_all([action, romance, external])
        await db.flush()
        showtimes = [
            Showtime(
                movie_id=movie.id,
                cinema_id=catalogue["cinema_id"],
                start_time=utc_now() + timedelta(days=1, hours=index),
                room_rows=0,
                room_cols=0,
                booking_mode=BookingMode.EXTERNAL_REDIRECT.value,
                external_booking_url=f"https://provider.example/{movie.id}",
                source="galaxy",
                external_id=f"behavior-showtime-{movie.id}",
            )
            for index, movie in enumerate([action, romance, external])
        ]
        db.add_all(showtimes)
        await db.flush()
        external_showtime_id = showtimes[-1].id
        await db.commit()
        return action.id, romance.id, external_showtime_id


@pytest.mark.asyncio
async def test_events_require_login_and_deduplicate(client, catalogue):
    payload = {"event_type": "movie_viewed", "movie_id": catalogue["movie_id"]}
    unauthorized = await client.post("/events", json=payload)
    assert unauthorized.status_code == 401

    await register_and_login(client, "behavior@example.com")
    created = await client.post("/events", json=payload)
    duplicate = await client.post("/events", json=payload)

    assert created.status_code == 201
    assert created.json()["deduplicated"] is False
    assert duplicate.status_code == 200
    assert duplicate.json()["deduplicated"] is True
    events = await client.get("/events/me")
    assert len(events.json()) == 1


@pytest.mark.asyncio
async def test_external_click_derives_provider_context(client, catalogue):
    _action_id, _romance_id, external_showtime_id = (
        await _add_recommendation_candidates(catalogue)
    )
    await register_and_login(client, "external-event@example.com")

    response = await client.post(
        "/events",
        json={
            "event_type": "external_booking_clicked",
            "showtime_id": external_showtime_id,
        },
    )

    assert response.status_code == 201
    event = response.json()["event"]
    assert event["showtime_id"] == external_showtime_id
    assert event["cinema_id"] == catalogue["cinema_id"]
    assert event["source"] == "galaxy"
    assert event["movie_id"] is not None


@pytest.mark.asyncio
async def test_behavior_profile_recommends_similar_available_movie(client, catalogue):
    action_id, romance_id, _external_showtime_id = (
        await _add_recommendation_candidates(catalogue)
    )
    await register_and_login(client, "recommendation@example.com")
    await client.post(
        "/events",
        json={"event_type": "movie_viewed", "movie_id": catalogue["movie_id"]},
    )

    response = await client.get("/recommendations/me", params={"limit": 3})

    assert response.status_code == 200
    recommended_ids = [item["movie"]["id"] for item in response.json()]
    assert catalogue["movie_id"] not in recommended_ids
    assert recommended_ids[0] == action_id
    assert romance_id in recommended_ids


@pytest.mark.asyncio
async def test_cold_start_returns_trending_movies(client, catalogue):
    await _add_recommendation_candidates(catalogue)
    await register_and_login(client, "cold-start@example.com")

    response = await client.get("/recommendations/me", params={"limit": 3})

    assert response.status_code == 200
    assert len(response.json()) == 3


@pytest.mark.asyncio
async def test_natural_language_recommendation_and_click_tracking(
    monkeypatch, client, catalogue
):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    action_id, _romance_id, _external_showtime_id = (
        await _add_recommendation_candidates(catalogue)
    )
    unauthorized = await client.post(
        "/recommendations/natural-language",
        json={"prompt": "Tôi muốn xem phim hành động phiêu lưu bùng nổ"},
    )
    assert unauthorized.status_code == 401

    await register_and_login(client, "semantic@example.com")
    response = await client.post(
        "/recommendations/natural-language",
        json={
            "prompt": "Tôi muốn xem phim action phiêu lưu với anh hùng và cháy nổ",
            "latitude": 10.7769,
            "longitude": 106.7009,
            "radius_km": 5,
            "limit": 3,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["engine"] == "local_tfidf_fallback"
    assert payload["results"][0]["movie"]["id"] == action_id
    assert len(payload["context_id"]) == 36
    nearest = payload["results"][0]["nearest_showtime"]
    assert nearest["cinema_id"] == catalogue["cinema_id"]
    assert nearest["distance_km"] == 0
    assert nearest["booking_url"].startswith("https://provider.example/")

    click = await client.post(
        "/events",
        json={
            "event_type": "recommendation_clicked",
            "movie_id": action_id,
            "context_id": payload["context_id"],
        },
    )
    assert click.status_code == 201
    events = (await client.get("/events/me")).json()
    assert {event["event_type"] for event in events} == {
        "preference_prompt_submitted",
        "recommendation_clicked",
    }


@pytest.mark.asyncio
async def test_natural_language_location_validation_and_radius(client, catalogue):
    await _add_recommendation_candidates(catalogue)
    await register_and_login(client, "semantic-radius@example.com")

    incomplete = await client.post(
        "/recommendations/natural-language",
        json={
            "prompt": "Tôi muốn xem phim gia đình vui vẻ",
            "latitude": 10.7769,
        },
    )
    assert incomplete.status_code == 422

    outside_radius = await client.post(
        "/recommendations/natural-language",
        json={
            "prompt": "Tôi muốn xem phim gia đình vui vẻ",
            "latitude": 21.0285,
            "longitude": 105.8542,
            "radius_km": 1,
        },
    )
    assert outside_radius.status_code == 200
    assert outside_radius.json()["results"] == []


@pytest.mark.asyncio
async def test_gemini_vectors_are_cached(monkeypatch, catalogue):
    await _add_recommendation_candidates(catalogue)
    calls: list[int] = []

    async def fake_embeddings(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        return [
            [1.0, 0.0] if "action" in text.lower() else [0.0, 1.0]
            for text in texts
        ]

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "GEMINI_EMBEDDING_MODEL", "test-model")
    monkeypatch.setattr(
        semantic_recommendation, "_request_embeddings", fake_embeddings
    )

    async with AsyncSessionLocal() as db:
        movies = list((await db.scalars(select(Movie))).all())
        first_scores, first_engine = await semantic_recommendation.get_semantic_scores(
            db, movies, "action adventure"
        )
        await db.commit()
        second_scores, second_engine = await semantic_recommendation.get_semantic_scores(
            db, movies, "action adventure"
        )

    assert first_engine == second_engine == "gemini_embedding"
    assert calls == [len(movies) + 1]
    assert first_scores == second_scores


@pytest.mark.asyncio
async def test_gemini_embedding_request_uses_official_batch_contract(monkeypatch):
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == (
            "https://generativelanguage.googleapis.com/v1beta/"
            "models/gemini-embedding-2:batchEmbedContents"
        )
        assert request.headers["x-goog-api-key"] == "gemini-test-key"
        payload = json.loads(request.content)
        assert payload == {
            "requests": [
                {
                    "model": "models/gemini-embedding-2",
                    "content": {"parts": [{"text": "phim gia đình"}]},
                },
                {
                    "model": "models/gemini-embedding-2",
                    "content": {"parts": [{"text": "phim hành động"}]},
                },
            ]
        }
        return httpx.Response(
            200,
            json={"embeddings": [{"values": [1, 0]}, {"values": [0, 1]}]},
        )

    def fake_async_client(*_args, **kwargs):
        return real_async_client(
            transport=httpx.MockTransport(handler),
            timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setattr(settings, "GEMINI_EMBEDDING_MODEL", "gemini-embedding-2")
    monkeypatch.setattr(
        semantic_recommendation.httpx, "AsyncClient", fake_async_client
    )

    vectors = await semantic_recommendation._request_embeddings(
        ["phim gia đình", "phim hành động"]
    )

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]


@pytest.mark.asyncio
async def test_semantic_daily_quota_is_enforced(
    monkeypatch, client, catalogue
):
    await _add_recommendation_candidates(catalogue)
    await register_and_login(client, "semantic-quota@example.com")
    monkeypatch.setattr(settings, "AI_REQUESTS_PER_USER_PER_DAY", 2)
    monkeypatch.setattr(settings, "AI_REQUESTS_PER_IP_PER_DAY", 100)
    request = {
        "prompt": "Tôi muốn xem một phim hành động vui vẻ",
        "limit": 2,
    }

    first = await client.post("/recommendations/natural-language", json=request)
    second = await client.post("/recommendations/natural-language", json=request)
    blocked = await client.post("/recommendations/natural-language", json=request)

    assert first.status_code == second.status_code == 200
    assert first.json()["quota_remaining"] == 1
    assert second.json()["quota_remaining"] == 0
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


@pytest.mark.asyncio
async def test_distributed_lock_has_single_owner():
    async with distributed_lock("test-collector", ttl_seconds=30) as first:
        assert first is True
        async with distributed_lock("test-collector", ttl_seconds=30) as second:
            assert second is False
    async with distributed_lock("test-collector", ttl_seconds=30) as acquired_again:
        assert acquired_again is True
