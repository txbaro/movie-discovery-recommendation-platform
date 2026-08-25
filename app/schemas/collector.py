from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class CollectorFreshnessRead(BaseModel):
    source: str
    freshness_status: Literal["fresh", "stale", "unknown"]
    last_successful_at: datetime | None
    age_hours: float | None
    last_run_status: str | None
    last_run_at: datetime | None
    last_collected_count: int | None
    warning: bool

