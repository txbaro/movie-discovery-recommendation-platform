from datetime import timedelta

import pytest

from app.core.database import AsyncSessionLocal
from app.models.collector_run import CollectorRunStatus
from app.services.cinema_sync import SyncResult
from app.services.collector_monitoring import (
    classify_sync_result,
    finish_collector_run,
    start_collector_run,
    utc_now,
)


def test_sync_result_classification():
    assert classify_sync_result(SyncResult(collected=8, created=8)) == (
        CollectorRunStatus.SUCCESS
    )
    assert classify_sync_result(SyncResult()) == CollectorRunStatus.SUSPICIOUS
    assert classify_sync_result(SyncResult(collected=8, failed=2)) == (
        CollectorRunStatus.PARTIAL_FAILURE
    )
    assert classify_sync_result(SyncResult(collected=2, failed=2)) == (
        CollectorRunStatus.FAILED
    )


@pytest.mark.asyncio
async def test_freshness_reports_success_stale_and_suspicious_sources(client):
    async with AsyncSessionLocal() as db:
        cinestar = await start_collector_run(db, "cinestar", None, 7)
        await finish_collector_run(
            db,
            cinestar,
            status=CollectorRunStatus.SUCCESS,
            result=SyncResult(collected=20, created=20),
        )

        lotte = await start_collector_run(db, "lotte", None, 7)
        await finish_collector_run(
            db,
            lotte,
            status=CollectorRunStatus.SUSPICIOUS,
            result=SyncResult(),
        )

        galaxy = await start_collector_run(db, "galaxy", None, 7)
        galaxy.started_at = utc_now() - timedelta(hours=10)
        galaxy.finished_at = utc_now() - timedelta(hours=10)
        galaxy.status = CollectorRunStatus.SUCCESS.value
        galaxy.collected_count = 15
        await db.commit()

    response = await client.get("/collectors/freshness")
    assert response.status_code == 200
    by_source = {item["source"]: item for item in response.json()}

    assert by_source["cinestar"]["freshness_status"] == "fresh"
    assert by_source["cinestar"]["last_collected_count"] == 20
    assert by_source["cinestar"]["warning"] is False

    assert by_source["lotte"]["freshness_status"] == "unknown"
    assert by_source["lotte"]["last_run_status"] == "suspicious"
    assert by_source["lotte"]["warning"] is True

    assert by_source["galaxy"]["freshness_status"] == "stale"
    assert by_source["galaxy"]["age_hours"] >= 9.9
    assert by_source["galaxy"]["warning"] is True


@pytest.mark.asyncio
async def test_home_page_displays_collector_freshness(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "DATA FRESHNESS" in response.text
    assert "Chưa có lần đồng bộ thành công" in response.text
