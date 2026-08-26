import asyncio
import json

from app.core.database import AsyncSessionLocal, engine
from app.services.tmdb import enrich_movie_ratings


async def main() -> None:
    """Backfill or refresh TMDB ratings for every canonical movie."""
    try:
        async with AsyncSessionLocal() as db:
            result = await enrich_movie_ratings(db)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
