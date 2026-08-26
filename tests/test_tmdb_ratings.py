import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.movie import Movie
from app.services import tmdb


@pytest.mark.asyncio
async def test_enrichment_uses_tmdb_votes_and_rejects_wrong_runtime(monkeypatch):
    async with AsyncSessionLocal() as db:
        db.add_all(
            [
                Movie(
                    title="Mai",
                    genres="Drama",
                    description="Vietnamese movie",
                    duration_minutes=131,
                    rating=9.9,
                ),
                Movie(
                    title="No Rating",
                    genres="Drama",
                    description="No TMDB votes",
                    duration_minutes=100,
                    rating=8.8,
                ),
                Movie(
                    title="Wrong Runtime",
                    genres="Drama",
                    description="Should not match",
                    duration_minutes=100,
                    rating=7.7,
                ),
            ]
        )
        await db.commit()

    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/movie":
            title = request.url.params["query"]
            ids = {"Mai": 1, "No Rating": 2, "Wrong Runtime": 3}
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": ids[title], "title": title, "original_title": title}
                    ]
                },
            )
        movie_id = int(request.url.path.rsplit("/", 1)[-1])
        details = {
            1: {
                "id": 1,
                "title": "Mai",
                "runtime": 131,
                "vote_average": 7.24,
                "vote_count": 250,
            },
            2: {
                "id": 2,
                "title": "No Rating",
                "runtime": 100,
                "vote_average": 0,
                "vote_count": 0,
            },
            3: {
                "id": 3,
                "title": "Wrong Runtime",
                "runtime": 200,
                "vote_average": 9.5,
                "vote_count": 1000,
            },
        }
        return httpx.Response(200, json=details[movie_id])

    def fake_async_client(*_args, **kwargs):
        return real_async_client(
            transport=httpx.MockTransport(handler),
            timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr(settings, "TMDB_API_KEY", "test-key")
    monkeypatch.setattr(tmdb.httpx, "AsyncClient", fake_async_client)

    async with AsyncSessionLocal() as db:
        result = await tmdb.enrich_movie_ratings(db)
        movies = {
            movie.title: movie
            for movie in (await db.scalars(select(Movie))).all()
        }

    assert result == {
        "processed": 3,
        "matched": 2,
        "rated": 1,
        "unrated": 2,
        "failed": 0,
        "status": "success",
    }
    assert movies["Mai"].rating == 7.2
    assert movies["Mai"].rating_vote_count == 250
    assert movies["Mai"].rating_source == "tmdb"
    assert movies["No Rating"].rating is None
    assert movies["No Rating"].rating_source == "tmdb"
    assert movies["Wrong Runtime"].rating is None
    assert movies["Wrong Runtime"].tmdb_id is None


@pytest.mark.asyncio
async def test_homepage_only_displays_tmdb_rating(client, catalogue):
    response = await client.get("/")
    assert response.status_code == 200
    assert "Chưa đánh giá" in response.text

    async with AsyncSessionLocal() as db:
        movie = await db.get(Movie, catalogue["movie_id"])
        movie.tmdb_id = 123
        movie.rating_source = "tmdb"
        movie.rating_vote_count = 42
        await db.commit()

    response = await client.get("/")
    assert response.status_code == 200
    assert "⭐ 8.5" in response.text
