"""
Đọc cấu hình từ file .env.
Dùng pydantic-settings để tự động validate và parse biến môi trường.
"""
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    APP_BASE_URL: str = "http://localhost:8001"
    COOKIE_SECURE: bool = False
    SQL_ECHO: bool = False
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    TMDB_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-2"
    GEMINI_API_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
    REDIS_URL: str = "redis://localhost:6379/0"
    AI_REQUESTS_PER_USER_PER_DAY: int = 20
    AI_REQUESTS_PER_IP_PER_DAY: int = 100
    AI_PROMPT_CACHE_TTL_SECONDS: int = 86400
    COLLECTOR_LOCK_TTL_SECONDS: int = 3600
    COLLECTOR_FRESHNESS_HOURS: int = 8
    COLLECTOR_SCHEDULER_SOURCES: str = "cinestar,lotte,galaxy"
    COLLECTOR_SCHEDULE_INTERVAL_MINUTES: int = 360
    COLLECTOR_STAGGER_MINUTES: int = 10
    COLLECTOR_SYNC_DAYS: int = 7
    ENABLE_INTERNAL_BOOKING: bool = False

    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_USE_TLS: bool = True

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def use_async_postgres_driver(cls, value: str) -> str:
        """Render provides a sync URL; the application uses asyncpg."""
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        return value

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# Singleton — import cái này ở nơi khác thay vì tạo Settings() nhiều lần
settings = Settings()
