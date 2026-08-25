"""Long-running scheduler process for live cinema collectors."""

import argparse
import asyncio
import logging
import signal
from collections.abc import Sequence
from time import monotonic

from app.core.config import settings
from app.core.database import engine
from app.services.discovery import VIETNAM_TIMEZONE, utc_now
from app.scripts.sync_cinema_data import COLLECTORS, sync_source


logger = logging.getLogger("collector_scheduler")


def configured_sources(raw_sources: str) -> tuple[str, ...]:
    sources = tuple(
        source.strip().lower()
        for source in raw_sources.split(",")
        if source.strip()
    )
    if not sources:
        raise ValueError("COLLECTOR_SCHEDULER_SOURCES must not be empty")
    invalid = sorted(set(sources) - set(COLLECTORS))
    if invalid:
        raise ValueError(f"Unsupported scheduler sources: {', '.join(invalid)}")
    if len(set(sources)) != len(sources):
        raise ValueError("COLLECTOR_SCHEDULER_SOURCES must not contain duplicates")
    return sources


def validate_schedule(interval_minutes: int, stagger_minutes: int, days: int) -> None:
    if interval_minutes < 1:
        raise ValueError("COLLECTOR_SCHEDULE_INTERVAL_MINUTES must be positive")
    if stagger_minutes < 0:
        raise ValueError("COLLECTOR_STAGGER_MINUTES must not be negative")
    if not 1 <= days <= 31:
        raise ValueError("COLLECTOR_SYNC_DAYS must be between 1 and 31")


async def run_provider_once(source: str, days: int) -> bool:
    target_date = utc_now().astimezone(VIETNAM_TIMEZONE).date()
    logger.info("Starting source=%s target_date=%s days=%s", source, target_date, days)
    try:
        result = await sync_source(source, target_date, days)
    except Exception:
        # sync_source has already persisted the failed CollectorRun.
        logger.exception("Collector failed source=%s", source)
        return False
    logger.info("Collector completed source=%s result=%s", source, result)
    return result.get("status") not in {"failed", "suspicious"}


async def wait_or_stop(stop_event: asyncio.Event, seconds: float) -> bool:
    if seconds <= 0:
        return stop_event.is_set()
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return True
    except TimeoutError:
        return False


async def provider_loop(
    source: str,
    *,
    initial_delay_seconds: int,
    interval_seconds: int,
    days: int,
    stop_event: asyncio.Event,
) -> None:
    if await wait_or_stop(stop_event, initial_delay_seconds):
        return

    while not stop_event.is_set():
        started = monotonic()
        await run_provider_once(source, days)
        elapsed = monotonic() - started
        if await wait_or_stop(stop_event, max(1, interval_seconds - elapsed)):
            return


async def run_once(sources: Sequence[str], days: int) -> bool:
    outcomes = []
    for source in sources:
        outcomes.append(await run_provider_once(source, days))
    return all(outcomes)


async def run_scheduler(*, once: bool = False) -> bool:
    sources = configured_sources(settings.COLLECTOR_SCHEDULER_SOURCES)
    validate_schedule(
        settings.COLLECTOR_SCHEDULE_INTERVAL_MINUTES,
        settings.COLLECTOR_STAGGER_MINUTES,
        settings.COLLECTOR_SYNC_DAYS,
    )
    logger.info(
        "Scheduler configured sources=%s interval_minutes=%s stagger_minutes=%s days=%s",
        ",".join(sources),
        settings.COLLECTOR_SCHEDULE_INTERVAL_MINUTES,
        settings.COLLECTOR_STAGGER_MINUTES,
        settings.COLLECTOR_SYNC_DAYS,
    )
    if once:
        return await run_once(sources, settings.COLLECTOR_SYNC_DAYS)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(shutdown_signal, stop_event.set)

    interval_seconds = settings.COLLECTOR_SCHEDULE_INTERVAL_MINUTES * 60
    stagger_seconds = settings.COLLECTOR_STAGGER_MINUTES * 60
    tasks = [
        asyncio.create_task(
            provider_loop(
                source,
                initial_delay_seconds=index * stagger_seconds,
                interval_seconds=interval_seconds,
                days=settings.COLLECTOR_SYNC_DAYS,
                stop_event=stop_event,
            ),
            name=f"collector-scheduler-{source}",
        )
        for index, source in enumerate(sources)
    ]
    await asyncio.gather(*tasks)
    logger.info("Collector scheduler stopped")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run scheduled cinema collectors")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run configured sources once without staggering, then exit",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    try:
        successful = await run_scheduler(once=args.once)
    finally:
        await engine.dispose()
    if not successful:
        raise SystemExit(1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(main())
