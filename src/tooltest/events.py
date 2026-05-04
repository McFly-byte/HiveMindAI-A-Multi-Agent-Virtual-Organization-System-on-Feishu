from __future__ import annotations

from dataclasses import dataclass, field, asdict
from queue import Queue, Empty
from typing import Any
import time
import uuid


@dataclass
class Event:
    type: str
    source: str
    payload: dict[str, Any]
    call_id: str | None = None
    job_id: str | None = None
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, dict[str, Any]] = {}
        self._events: list[Event] = []

    def subscribe(self, subscriber_id: str, event_types: list[str]):
        self._subscribers[subscriber_id] = {
            "event_types": set(event_types),
            "queue": Queue(),
        }

    def publish(self, event: Event):
        self._events.append(event)
        for sub in self._subscribers.values():
            event_types = sub["event_types"]
            if "*" in event_types or event.type in event_types:
                sub["queue"].put(event)

    def drain(self, subscriber_id: str) -> list[Event]:
        sub = self._subscribers.get(subscriber_id)
        if not sub:
            return []
        queue: Queue = sub["queue"]
        events: list[Event] = []
        while True:
            try:
                events.append(queue.get_nowait())
            except Empty:
                return events

    def recent(self, limit: int = 20) -> list[Event]:
        return self._events[-limit:]

    def subscriber_queue_sizes(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for sub_id, sub in self._subscribers.items():
            queue: Queue = sub["queue"]
            out[sub_id] = queue.qsize()
        return out

    def pending_for(self, subscriber_id: str) -> list[Event]:
        sub = self._subscribers.get(subscriber_id)
        if not sub:
            return []
        queue: Queue = sub["queue"]
        with queue.mutex:
            return list(queue.queue)
