from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..events import Event, EventBus
from ..spec import EventSourceSpec


class EventSource(Protocol):
    name: str

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def status(self) -> dict[str, Any]: ...


@dataclass
class EventSourceContext:
    spec: EventSourceSpec
    event_bus: EventBus

    def emit(self, event_type: str, payload: dict[str, Any], *, event_id: str | None = None, source: str | None = None) -> None:
        kwargs = {"event_id": event_id} if event_id else {}
        self.event_bus.publish(Event(
            type=event_type,
            source=source or self.spec.name,
            payload=payload,
            **kwargs,
        ))


class EventSourceManager:
    def __init__(self, sources: list[EventSource]) -> None:
        self.sources = sources

    def start_all(self) -> None:
        for source in self.sources:
            source.start()

    def stop_all(self) -> None:
        for source in reversed(self.sources):
            try:
                source.stop()
            except Exception:
                pass

    def status(self) -> list[dict[str, Any]]:
        return [source.status() for source in self.sources]
