from app.core.config import Settings


def test_render_postgres_url_uses_asyncpg_driver():
    settings = Settings(
        DATABASE_URL="postgresql://user:password@internal-host:5432/movies",
        SECRET_KEY="test-secret",
        _env_file=None,
    )

    assert settings.DATABASE_URL == (
        "postgresql+asyncpg://user:password@internal-host:5432/movies"
    )
