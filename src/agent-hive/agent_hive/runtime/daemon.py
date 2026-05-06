from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from agent_hive.events.models import HiveEvent
from agent_hive.events.sources.base import EventSource
from agent_hive.observability.logging import get_logger
from agent_hive.runtime.hive_runtime import HiveRuntime


logger = get_logger("runtime.daemon")


@dataclass
class DaemonStats:
    received_events: int = 0
    dispatched_events: int = 0
    failed_events: int = 0
    last_error: str | None = None


@dataclass
class EventDaemon:
    """Resident background loop that dispatches incoming messages to agents."""

    runtime: HiveRuntime
    sources: list[EventSource] = field(default_factory=list)
    queue_maxsize: int = 1000

    def __post_init__(self) -> None:
        self.queue: asyncio.Queue[HiveEvent] = asyncio.Queue(maxsize=self.queue_maxsize)
        self.stats = DaemonStats()
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    def add_source(self, source: EventSource) -> None:
        self.sources.append(source)
        logger.info("event source registered source=%s total_sources=%s", source.name, len(self.sources))

    async def emit(self, event: HiveEvent) -> None:
        self.stats.received_events += 1
        logger.info(
            "event received event_id=%s event_type=%s source=%s target_agent=%s queue_size=%s",
            event.event_id,
            event.event_type,
            event.source,
            event.target_agent_id,
            self.queue.qsize(),
        )
        await self.queue.put(event)

    async def run_forever(self) -> None:
        self._stop.clear()
        logger.info("daemon run loop starting sources=%s queue_maxsize=%s", [source.name for source in self.sources], self.queue_maxsize)
        self._tasks = [
            asyncio.create_task(source.run(self.emit), name=f"event-source:{source.name}")
            for source in self.sources
        ]
        self._tasks.append(asyncio.create_task(self._dispatch_loop(), name="event-dispatch-loop"))
        try:
            await self._stop.wait()
        except asyncio.CancelledError:
            logger.info("daemon run loop cancelled")
            raise
        finally:
            await self.stop()

    async def run_until_idle(self, *, timeout_seconds: float = 1.0) -> None:
        """Test helper: process queued events until queue.join or timeout."""

        worker = asyncio.create_task(self._dispatch_loop())
        try:
            await asyncio.wait_for(self.queue.join(), timeout=timeout_seconds)
        finally:
            self._stop.set()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def stop(self) -> None:
        if self._stop.is_set():
            pass
        logger.info(
            "daemon stopping received=%s dispatched=%s failed=%s",
            self.stats.received_events,
            self.stats.dispatched_events,
            self.stats.failed_events,
        )
        self._stop.set()
        for source in self.sources:
            await source.stop()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            event = await self.queue.get()
            try:
                logger.info(
                    "dispatching event event_id=%s event_type=%s target_agent=%s",
                    event.event_id,
                    event.event_type,
                    event.target_agent_id,
                )
                await self.runtime.dispatch(event)
                self.stats.dispatched_events += 1
                logger.info(
                    "event dispatched event_id=%s dispatched_count=%s",
                    event.event_id,
                    self.stats.dispatched_events,
                )
            except Exception as exc:  # noqa: BLE001 - daemon must keep serving later events.
                self.stats.failed_events += 1
                self.stats.last_error = str(exc)
                logger.exception("event dispatch failed event_id=%s error=%s", event.event_id, exc)
            finally:
                self.queue.task_done()
