import argparse
import asyncio
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.collectors.cinestar import CinestarCollector
from app.collectors.galaxy import GalaxyCollector
from app.collectors.lotte import LotteCollector
from app.core.database import AsyncSessionLocal, engine
from app.models.collector_run import CollectorRunStatus
from app.services.cinema_sync import sync_collected_showtimes
from app.services.collector_monitoring import (
    classify_sync_result,
    finish_collector_run,
    record_skipped_collector_run,
    start_collector_run,
)
from app.services.redis_features import distributed_lock


COLLECTORS = {
    "cinestar": CinestarCollector,
    "lotte": LotteCollector,
    "galaxy": GalaxyCollector,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synchronize cinema schedule data")
    parser.add_argument(
        "--source",
        choices=["cinestar", "lotte", "galaxy"],
        required=True,
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date(),
        help="Ngày YYYY-MM-DD; mặc định là hôm nay theo giờ Việt Nam",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Số ngày liên tiếp; mặc định 7, hỗ trợ từ 1 đến 31",
    )
    return parser.parse_args()


async def sync_source(source: str, target_date: date, days: int) -> dict[str, object]:
    """Run one observable, lock-protected provider synchronization."""
    if source not in COLLECTORS:
        raise ValueError(f"Unsupported collector source: {source}")
    if not 1 <= days <= 31:
        raise ValueError("days must be between 1 and 31")

    async with AsyncSessionLocal() as db:
        async with distributed_lock(f"collector:{source}") as acquired:
            if not acquired:
                run = await record_skipped_collector_run(
                    db,
                    source,
                    target_date,
                    days,
                    "collector_already_running",
                )
                return {
                    "status": "skipped",
                    "reason": "collector_already_running",
                    "collector_run_id": run.id,
                }

            run = await start_collector_run(db, source, target_date, days)
            run_id = run.id
            try:
                collector = COLLECTORS[source]()
                items = await collector.collect_range(target_date, days)
                result = await sync_collected_showtimes(
                    db, collector.source, items
                )
            except Exception as exc:
                await db.rollback()
                run = await db.get(type(run), run_id)
                if run is None:
                    raise RuntimeError(
                        f"Collector run {run_id} disappeared"
                    ) from exc
                await finish_collector_run(
                    db,
                    run,
                    status=CollectorRunStatus.FAILED,
                    error_message=str(exc),
                )
                raise

            status = classify_sync_result(result)
            run = await finish_collector_run(
                db, run, status=status, result=result
            )
            return {
                **result.model_dump(),
                "status": status.value,
                "collector_run_id": run.id,
            }


async def main() -> None:
    args = parse_args()
    days = args.days if args.days is not None else 7
    try:
        result = await sync_source(args.source, args.date, days)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
