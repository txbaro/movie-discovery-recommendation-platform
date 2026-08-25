from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.collector import CollectorFreshnessRead
from app.services.collector_monitoring import get_collector_freshness


router = APIRouter(prefix="/collectors", tags=["collector monitoring"])


@router.get("/freshness", response_model=list[CollectorFreshnessRead])
async def collector_freshness(
    db: AsyncSession = Depends(get_db),
) -> list[CollectorFreshnessRead]:
    """Expose safe provider freshness metadata without operational error details."""
    return await get_collector_freshness(db)

