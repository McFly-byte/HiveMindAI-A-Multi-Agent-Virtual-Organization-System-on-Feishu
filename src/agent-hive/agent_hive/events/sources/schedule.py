from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_hive.events.models import HiveEvent
from agent_hive.events.sources.base import EventEmitFn
from agent_hive.observability.logging import get_logger


logger = get_logger("events.schedule")


PayloadFactory = Callable[[], dict[str, Any]]


@dataclass
class ScheduledEventSource:
    """Periodic in-process event source for resident daemon jobs."""

    name: str
    event_type: str
    project_id: str
    target_agent_id: str | None
    payload_factory: PayloadFactory
    interval_seconds: float
    run_on_start: bool = True
    source_name: str = "schedule"
    _stop: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    async def run(self, emit: EventEmitFn) -> None:
        logger.info(
            "scheduled source starting name=%s event_type=%s interval_seconds=%s run_on_start=%s",
            self.name,
            self.event_type,
            self.interval_seconds,
            self.run_on_start,
        )
        if self.run_on_start:
            await self._emit_once(emit)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                await self._emit_once(emit)

    async def stop(self) -> None:
        logger.info("scheduled source stopping name=%s", self.name)
        self._stop.set()

    async def _emit_once(self, emit: EventEmitFn) -> HiveEvent:
        event = HiveEvent(
            event_type=self.event_type,
            project_id=self.project_id,
            source=self.source_name,
            target_agent_id=self.target_agent_id,
            payload=self.payload_factory(),
        )
        logger.info(
            "scheduled event emitted name=%s event_id=%s event_type=%s target_agent=%s",
            self.name,
            event.event_id,
            event.event_type,
            event.target_agent_id,
        )
        await emit(event)
        return event
