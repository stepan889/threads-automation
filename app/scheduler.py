import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import BotState
from app.repository import running_states
from app.services.workflow import PublishingWorkflow
from app.services.discovery import DiscoveryWorkflow

logger = logging.getLogger(__name__)


class AutopostScheduler:
    def __init__(self, sessions: async_sessionmaker, workflow: PublishingWorkflow,
                 discovery: DiscoveryWorkflow | None = None) -> None:
        self.sessions, self.workflow = sessions, workflow
        self.discovery = discovery
        self.scheduler = AsyncIOScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300})

    async def start(self) -> None:
        self.scheduler.start()
        async with self.sessions() as session:
            for state in await running_states(session):
                self.schedule(state.telegram_id, state.interval_minutes)

    def schedule(self, telegram_id: int, minutes: int) -> None:
        self.scheduler.add_job(self._run, "interval", minutes=minutes, id=f"autopost:{telegram_id}", args=[telegram_id], replace_existing=True)

    def stop(self, telegram_id: int) -> None:
        job = self.scheduler.get_job(f"autopost:{telegram_id}")
        if job:
            job.remove()

    async def _run(self, telegram_id: int) -> None:
        async with self.sessions() as session:
            state = await session.get(BotState, telegram_id, with_for_update=True)
            if state is None or not state.is_running:
                self.stop(telegram_id)
                return
            try:
                if self.discovery is not None:
                    await self.discovery.run(session, state)
                else:
                    await self.workflow.run(session, state)
            except Exception:
                await session.rollback()
                logger.exception("Autopost job failed for telegram_id=%s", telegram_id)

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
