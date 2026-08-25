from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.collector_run import CollectorRun, CollectorRunStatus
from app.schemas.collector import CollectorFreshnessRead
from app.services.cinema_sync import SyncResult


LIVE_COLLECTOR_SOURCES = ("cinestar", "lotte", "galaxy")
SUCCESSFUL_RUN_STATUSES = (
    CollectorRunStatus.SUCCESS.value,
    CollectorRunStatus.PARTIAL_FAILURE.value,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def start_collector_run(
    db: AsyncSession,
    source: str,
    target_date: date | None,
    days_requested: int,
) -> CollectorRun:
    run = CollectorRun(
        source=source,
        status=CollectorRunStatus.RUNNING.value,
        target_date=target_date,
        days_requested=days_requested,
        started_at=utc_now(),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


def classify_sync_result(result: SyncResult) -> CollectorRunStatus:
    if result.collected == 0:
        return CollectorRunStatus.SUSPICIOUS
    if result.failed == 0:
        return CollectorRunStatus.SUCCESS
    if result.failed < result.collected:
        return CollectorRunStatus.PARTIAL_FAILURE
    return CollectorRunStatus.FAILED


async def finish_collector_run(
    db: AsyncSession,
    run: CollectorRun,
    *,
    status: CollectorRunStatus,
    result: SyncResult | None = None,
    error_message: str | None = None,
) -> CollectorRun:
    finished_at = utc_now()
    run.status = status.value
    run.finished_at = finished_at
    run.duration_ms = max(0, int((finished_at - run.started_at).total_seconds() * 1000))
    if result is not None:
        run.collected_count = result.collected
        run.created_count = result.created
        run.updated_count = result.updated
        run.skipped_count = result.skipped
        run.failed_count = result.failed
        if result.errors and error_message is None:
            error_message = "\n".join(result.errors)
    run.error_message = error_message[:8000] if error_message else None
    await db.commit()
    await db.refresh(run)
    return run


async def record_skipped_collector_run(
    db: AsyncSession,
    source: str,
    target_date: date | None,
    days_requested: int,
    reason: str,
) -> CollectorRun:
    run = await start_collector_run(db, source, target_date, days_requested)
    return await finish_collector_run(
        db,
        run,
        status=CollectorRunStatus.SKIPPED,
        error_message=reason,
    )


async def get_collector_freshness(
    db: AsyncSession,
    sources: tuple[str, ...] = LIVE_COLLECTOR_SOURCES,
) -> list[CollectorFreshnessRead]:
    now = utc_now()
    threshold_hours = settings.COLLECTOR_FRESHNESS_HOURS
    rows: list[CollectorFreshnessRead] = []

    for source in sources:
        latest_run = await db.scalar(
            select(CollectorRun)
            .where(CollectorRun.source == source)
            .order_by(CollectorRun.started_at.desc(), CollectorRun.id.desc())
            .limit(1)
        )
        latest_success = await db.scalar(
            select(CollectorRun)
            .where(
                CollectorRun.source == source,
                CollectorRun.status.in_(SUCCESSFUL_RUN_STATUSES),
                CollectorRun.finished_at.is_not(None),
            )
            .order_by(CollectorRun.finished_at.desc(), CollectorRun.id.desc())
            .limit(1)
        )

        age_hours = None
        freshness_status = "unknown"
        if latest_success is not None and latest_success.finished_at is not None:
            age_hours = max(
                0.0,
                (now - latest_success.finished_at).total_seconds() / 3600,
            )
            freshness_status = "fresh" if age_hours <= threshold_hours else "stale"

        last_run_status = latest_run.status if latest_run else None
        warning = freshness_status != "fresh" or last_run_status in {
            CollectorRunStatus.FAILED.value,
            CollectorRunStatus.SUSPICIOUS.value,
        }
        rows.append(
            CollectorFreshnessRead(
                source=source,
                freshness_status=freshness_status,
                last_successful_at=(
                    latest_success.finished_at if latest_success else None
                ),
                age_hours=round(age_hours, 1) if age_hours is not None else None,
                last_run_status=last_run_status,
                last_run_at=(latest_run.finished_at or latest_run.started_at)
                if latest_run
                else None,
                last_collected_count=(
                    latest_run.collected_count if latest_run else None
                ),
                warning=warning,
            )
        )
    return rows

