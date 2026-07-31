from unittest.mock import MagicMock

from app.scheduler import AutopostScheduler


import pytest


@pytest.mark.asyncio
async def test_scheduler_replaces_and_stops_jobs() -> None:
    service = AutopostScheduler(MagicMock(), MagicMock())
    service.scheduler.start(paused=True)
    try:
        service.schedule(1, 30)
        service.schedule(1, 60)
        job = service.scheduler.get_job("autopost:1")
        assert job is not None
        assert str(job.trigger).endswith("1:00:00]")
        service.stop(1)
        assert service.scheduler.get_job("autopost:1") is None
    finally:
        service.shutdown()
