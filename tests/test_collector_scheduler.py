import pytest

from app.scripts import run_collector_scheduler as scheduler


def test_configured_sources_normalizes_and_validates_values():
    assert scheduler.configured_sources(" Cinestar,LOTTE, galaxy ") == (
        "cinestar",
        "lotte",
        "galaxy",
    )

    with pytest.raises(ValueError, match="Unsupported"):
        scheduler.configured_sources("cinestar,cgv")

    with pytest.raises(ValueError, match="duplicates"):
        scheduler.configured_sources("lotte,lotte")


def test_schedule_configuration_validation():
    scheduler.validate_schedule(360, 10, 7)

    with pytest.raises(ValueError, match="INTERVAL"):
        scheduler.validate_schedule(0, 10, 7)
    with pytest.raises(ValueError, match="STAGGER"):
        scheduler.validate_schedule(360, -1, 7)
    with pytest.raises(ValueError, match="SYNC_DAYS"):
        scheduler.validate_schedule(360, 10, 32)


@pytest.mark.asyncio
async def test_run_once_continues_after_one_provider_fails(monkeypatch):
    calls = []

    async def fake_sync_source(source, target_date, days):
        calls.append((source, days))
        if source == "lotte":
            raise RuntimeError("upstream unavailable")
        return {"status": "success"}

    monkeypatch.setattr(scheduler, "sync_source", fake_sync_source)

    successful = await scheduler.run_once(("cinestar", "lotte", "galaxy"), 7)

    assert successful is False
    assert calls == [("cinestar", 7), ("lotte", 7), ("galaxy", 7)]
