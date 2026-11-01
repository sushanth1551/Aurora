"""Optional in-process scheduler for Search Console syncs and analyses.

**Disabled by default** (``GSC_SCHEDULER_ENABLED=false``). A schedule runs only when
all of these hold: the scheduler is enabled, the schedule is enabled, and the
schedule carries an approval of its own request ceiling. Each run is capped by that
ceiling; a schedule never inherits another schedule's or the manual sync's budget.
It makes read-only Search Console requests and never touches published content.

``run_due`` is a plain coroutine so tests drive it directly; ``run_forever`` is the
background loop used by the API process.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import AuroraException
from app.monitoring.service import GscService
from app.schemas.research import utc_now
from app.tools.errors import ProviderError

logger = logging.getLogger(__name__)

ServiceBuilder = Callable[[AsyncSession], GscService]


class GscScheduler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        build_service: ServiceBuilder,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = session_factory
        self._settings = settings
        self._build = build_service
        self._clock = clock

    async def run_due(self) -> list[dict[str, Any]]:
        """Run every due, approved, enabled schedule once; returns what happened."""
        if not self._settings.gsc_scheduler_enabled:
            return []  # defence in depth: the loop is never started when disabled
        results: list[dict[str, Any]] = []
        async with self._sessions() as session:
            service = self._build(session)
            repo = service.repository
            now = self._clock()
            for schedule in await repo.due_schedules(now):
                # Claim first: advance next_run_at so a slow run is never started twice.
                schedule.next_run_at = now + timedelta(hours=schedule.interval_hours)
                await repo.save(schedule)
                error: str | None = None
                try:
                    status = await service.run_schedule(schedule)
                except (AuroraException, ProviderError) as exc:
                    status, error = "failed", f"{exc.code}: {exc.message}"
                except Exception as exc:  # noqa: BLE001 - recorded, never leaked
                    logger.error(
                        "gsc_schedule_failed id=%s error=%s",
                        schedule.id,
                        type(exc).__name__,
                    )
                    status, error = "failed", "The scheduled run failed unexpectedly."
                schedule.last_run_at, schedule.last_status, schedule.last_error = (
                    now,
                    status,
                    error,
                )
                await repo.save(schedule)
                results.append(
                    {
                        "schedule_id": str(schedule.id),
                        "kind": schedule.kind,
                        "status": status,
                        "error": error,
                    }
                )
        return results

    async def run_forever(self, stop: asyncio.Event) -> None:
        tick = self._settings.gsc_scheduler_tick_seconds
        logger.info("gsc_scheduler_started tick_seconds=%s", tick)
        while not stop.is_set():
            try:
                await self.run_due()
            except Exception as exc:  # noqa: BLE001 - the loop must survive one bad tick
                logger.error("gsc_scheduler_tick_failed error=%s", type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=tick)
            except TimeoutError:
                continue
        logger.info("gsc_scheduler_stopped")
