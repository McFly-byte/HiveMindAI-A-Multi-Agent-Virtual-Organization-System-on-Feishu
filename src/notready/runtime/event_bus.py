from __future__ import annotations

from collections import defaultdict
from typing import Callable

from runtime.models import Event


EventHandler = Callable[[Event], None]


class EventBus:
    def __init__(self, *, on_enqueue: Callable[[Event], None] | None = None, on_processed: Callable[[Event], None] | None = None) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._on_enqueue = on_enqueue
        self._on_processed = on_processed

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        if self._on_enqueue:
            self._on_enqueue(event)
        for handler in self._handlers.get(event.event_type, []):
            handler(event)
        if self._on_processed:
            self._on_processed(event)
