from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from agent_hive.events.models import HiveEvent


@dataclass
class EventBus:
    """Small in-process event bus for orchestration and tests."""

    _subscribers: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _queues: dict[str, list[HiveEvent]] = field(default_factory=lambda: defaultdict(list))

    def subscribe(self, subscriber_id: str, event_types: list[str]) -> None:
        for event_type in event_types:
            self._subscribers[event_type].add(subscriber_id)

    def publish(self, event: HiveEvent) -> None:
        targets = set(self._subscribers.get(event.event_type, set()))
        targets.update(self._subscribers.get("*", set()))
        for target in targets:
            self._queues[target].append(event)

    def drain(self, subscriber_id: str) -> list[HiveEvent]:
        events = list(self._queues.get(subscriber_id, []))
        self._queues[subscriber_id].clear()
        return events
