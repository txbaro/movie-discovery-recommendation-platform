"""
Service gọi TMDB (The Movie Database) API để lấy dữ liệu phim thật.

Tách riêng thành module này (thay vì gọi thẳng trong route) để:
- Route chỉ lo việc HTTP request/response, không lẫn logic gọi API bên ngoài
- Dễ test độc lập, dễ thay đổi nguồn dữ liệu sau này (vd đổi sang nguồn khác)
  mà không phải sửa route
"""
from difflib import SequenceMatcher

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.movie import Movie, normalize_movie_title

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"


class TMDBError(Exception):
    """Raise khi gọi TMDB thất bại — route sẽ bắt exception này để trả lỗi rõ ràng."""

    pass


async def _get_genre_map(client: httpx.AsyncClient) -> dict[int, str]:
    """
    TMDB trả thể loại phim dưới dạng genre_id (số), không phải tên trực tiếp.
    Cần gọi endpoint riêng để lấy bảng ánh xạ id -> tên, vd {28: "Action", ...}
    """
    resp = await client.get(
        f"{TMDB_BASE_URL}/genre/movie/list",
        params={"api_key": settings.TMDB_API_KEY, "language": "en-US"},
    )
    if resp.status_code != 200:
        raise TMDBError(f"Không lấy được danh sách genre: {resp.status_code}")

    data = resp.json()
    return {g["id"]: g["name"] for g in data["genres"]}


async def _get_runtime(client: httpx.AsyncClient, tmdb_movie_id: int) -> int:
    """
    Endpoint danh sách phim (popular/now_playing) KHÔNG trả về thời lượng phim —
    phải gọi thêm endpoint chi tiết cho từng phim mới có runtime.
    Đây là lý do import sẽ hơi chậm nếu import nhiều phim cùng lúc
    (N phim = 1 request danh sách + N request chi tiết).
    """
    resp = await client.get(
        f"{TMDB_BASE_URL}/movie/{tmdb_movie_id}",
        params={"api_key": settings.TMDB_API_KEY},
    )
    if resp.status_code != 200:
        return 0  # không chặn cả quá trình import chỉ vì 1 phim lấy runtime lỗi
    return resp.json().get("runtime") or 0


async def fetch_movies(category: str = "popular", page: int = 1) -> list[dict]:
    """
    Lấy danh sách phim từ TMDB, đã chuyển đổi sẵn sang format khớp với
    Movie model của mình (title, genres, description, duration_minutes...).

    category: "popular" | "now_playing" | "top_rated" | "upcoming"
    """
    if not settings.TMDB_API_KEY:
        raise TMDBError(
            "Chưa cấu hình TMDB_API_KEY trong file .env. "
            "Đăng ký miễn phí tại https://www.themoviedb.org/settings/api"
        )

    async with httpx.AsyncClient(timeout=15.0) as client:
        genre_map = await _get_genre_map(client)

        resp = await client.get(
            f"{TMDB_BASE_URL}/movie/{category}",
            params={"api_key": settings.TMDB_API_KEY, "language": "en-US", "page": page},
        )
        if resp.status_code != 200:
            raise TMDBError(f"TMDB trả lỗi {resp.status_code}: {resp.text}")

        results = resp.json().get("results", [])

        movies = []
        for item in results:
            genre_names = [genre_map.get(gid, "") for gid in item.get("genre_ids", [])]
            genre_names = [g for g in genre_names if g]  # bỏ genre không map được

            runtime = await _get_runtime(client, item["id"])

            poster_path = item.get("poster_path")
            poster_url = f"{TMDB_IMAGE_BASE_URL}{poster_path}" if poster_path else None

            vote_count = int(item.get("vote_count") or 0)
            movies.append(
                {
                    "tmdb_id": item["id"],
                    "title": item.get("title", "Untitled"),
                    "genres": (
                        ",".join(genre_names) if genre_names else "Unknown"
                    ),
                    "description": item.get("overview") or "Chưa có mô tả.",
                    "duration_minutes": runtime,
                    "rating": (
                        round(float(item.get("vote_average") or 0.0), 1)
                        if vote_count > 0
                        else None
                    ),
                    "rating_vote_count": vote_count,
                    "rating_source": "tmdb",
                    "metadata_source": "tmdb",
                    "poster_url": poster_url,
                }
            )

        return movies


def _title_similarity(movie_title: str, candidate: dict) -> float:
    target = normalize_movie_title(movie_title)
    titles = {
        normalize_movie_title(candidate.get("title") or ""),
        normalize_movie_title(candidate.get("original_title") or ""),
    }
    titles.discard("")
    if target in titles:
        return 1.0
    return max(
        (SequenceMatcher(None, target, title).ratio() for title in titles),
        default=0.0,
    )


async def _get_movie_details(
    client: httpx.AsyncClient,
    tmdb_movie_id: int,
) -> dict | None:
    response = await client.get(
        f"{TMDB_BASE_URL}/movie/{tmdb_movie_id}",
        params={"api_key": settings.TMDB_API_KEY, "language": "vi-VN"},
    )
    if response.status_code != 200:
        return None
    return response.json()


async def _find_tmdb_match(
    client: httpx.AsyncClient,
    movie: Movie,
) -> dict | None:
    """Conservatively match a cinema title to TMDB using title and runtime."""
    if movie.tmdb_id is not None:
        return await _get_movie_details(client, movie.tmdb_id)

    response = await client.get(
        f"{TMDB_BASE_URL}/search/movie",
        params={
            "api_key": settings.TMDB_API_KEY,
            "query": movie.title,
            "language": "vi-VN",
            "include_adult": "false",
        },
    )
    if response.status_code != 200:
        raise TMDBError(
            f"TMDB search trả lỗi {response.status_code} cho {movie.title}"
        )

    candidates = sorted(
        response.json().get("results", [])[:10],
        key=lambda item: _title_similarity(movie.title, item),
        reverse=True,
    )[:3]
    best: tuple[float, dict] | None = None
    for candidate in candidates:
        title_score = _title_similarity(movie.title, candidate)
        if title_score < 0.9:
            continue
        details = await _get_movie_details(client, int(candidate["id"]))
        if details is None:
            continue
        runtime = int(details.get("runtime") or 0)
        runtime_delta = abs(runtime - movie.duration_minutes) if runtime else 0
        if runtime and runtime_delta > 15:
            continue
        # Exact titles win; for close titles, runtime breaks the tie and avoids
        # attaching a remake or another movie with a similar name.
        match_score = title_score - min(runtime_delta, 15) / 150
        if best is None or match_score > best[0]:
            best = (match_score, details)
    return best[1] if best is not None else None


async def enrich_movie_ratings(
    db: AsyncSession,
    movie_ids: set[int] | None = None,
) -> dict[str, int | str]:
    """Attach TMDB-only ratings to canonical movies and persist the result."""
    if not settings.TMDB_API_KEY:
        return {
            "processed": 0,
            "matched": 0,
            "rated": 0,
            "unrated": 0,
            "failed": 0,
            "status": "skipped_missing_api_key",
        }

    query = select(Movie).order_by(Movie.id)
    if movie_ids is not None:
        if not movie_ids:
            return {
                "processed": 0,
                "matched": 0,
                "rated": 0,
                "unrated": 0,
                "failed": 0,
                "status": "success",
            }
        query = query.where(Movie.id.in_(movie_ids))
    movies = list((await db.scalars(query)).all())
    stats: dict[str, int | str] = {
        "processed": len(movies),
        "matched": 0,
        "rated": 0,
        "unrated": 0,
        "failed": 0,
        "status": "success",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        for movie in movies:
            try:
                match = await _find_tmdb_match(client, movie)
                if match is None:
                    movie.rating = None
                    movie.rating_vote_count = None
                    movie.rating_source = None
                    stats["unrated"] += 1
                    continue

                tmdb_id = int(match["id"])
                owner = await db.scalar(
                    select(Movie.id).where(
                        Movie.tmdb_id == tmdb_id,
                        Movie.id != movie.id,
                    )
                )
                if owner is not None:
                    # A TMDB id is unique. Canonical merging should be handled
                    # separately instead of silently stealing the identifier.
                    stats["failed"] += 1
                    continue

                vote_count = int(match.get("vote_count") or 0)
                movie.tmdb_id = tmdb_id
                movie.rating_vote_count = vote_count
                movie.rating_source = "tmdb"
                movie.rating = (
                    round(float(match.get("vote_average") or 0.0), 1)
                    if vote_count > 0
                    else None
                )
                stats["matched"] += 1
                if movie.rating is None:
                    stats["unrated"] += 1
                else:
                    stats["rated"] += 1
            except (httpx.HTTPError, TMDBError, TypeError, ValueError):
                stats["failed"] += 1

    await db.commit()
    if stats["failed"]:
        stats["status"] = "partial_failure"
    return stats


async def get_trailer_key(tmdb_movie_id: int) -> str | None:
    """
    Lấy YouTube video key của trailer chính thức từ TMDB.
    Trả về None nếu phim không có trailer, hoặc chưa cấu hình TMDB_API_KEY.

    Chỉ trả về link YOUTUBE EMBED, không tải file video nào về server —
    tôn trọng đúng điều khoản sử dụng của TMDB (không cho phép host lại
    video, chỉ được embed qua YouTube).
    """
    if not settings.TMDB_API_KEY:
        return None

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{TMDB_BASE_URL}/movie/{tmdb_movie_id}/videos",
            params={"api_key": settings.TMDB_API_KEY, "language": "en-US"},
        )
        if resp.status_code != 200:
            return None

        results = resp.json().get("results", [])
        trailers = [
            v for v in results
            if v.get("site") == "YouTube" and v.get("type") == "Trailer"
        ]
        return trailers[0]["key"] if trailers else None
