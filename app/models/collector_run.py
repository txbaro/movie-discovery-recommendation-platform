from datetime import date, datetime
from enum import Enum

from sqlalchemy import CheckConstraint, Date, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CollectorRunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"
    SUSPICIOUS = "suspicious"
    SKIPPED = "skipped"


class CollectorRun(Base):
    """Durable audit record for one provider synchronization attempt."""

    __tablename__ = "collector_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'partial_failure', "
            "'failed', 'suspicious', 'skipped')",
            name="ck_collector_runs_status",
        ),
        Index("ix_collector_runs_source_started_at", "source", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=CollectorRunStatus.RUNNING.value
    )
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    days_requested: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    collected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
