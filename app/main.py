import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.routes import (
    auth,
    booking,
    cinemas,
    collectors,
    events,
    movies,
    pages,
    recommendations,
    showtimes,
    ws,
)
from app.services.redis_listener import listen_for_expired_holds


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Quản lý background task; database schema được quản lý bởi Alembic."""
    listener_task = None
    if settings.ENABLE_INTERNAL_BOOKING:
        listener_task = asyncio.create_task(listen_for_expired_holds())
    try:
        yield
    finally:
        if listener_task is not None:
            listener_task.cancel()
            with suppress(asyncio.CancelledError):
                await listener_task


app = FastAPI(title="Movie Booking System", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(booking.router)
app.include_router(cinemas.router)
app.include_router(collectors.router)
app.include_router(movies.router)
app.include_router(events.router)
app.include_router(recommendations.router)
app.include_router(showtimes.router)
if settings.ENABLE_INTERNAL_BOOKING:
    app.include_router(ws.router)


@app.get("/health")
async def health_check():
    """Endpoint đơn giản để kiểm tra server có chạy không."""
    return {"status": "ok"}
