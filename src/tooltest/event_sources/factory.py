from __future__ import annotations

from ..events import EventBus
from ..spec import EventSourceSpec
from .base import EventSource, EventSourceManager


def create_event_source(spec: EventSourceSpec, event_bus: EventBus) -> EventSource:
    if spec.type == "feishu.websocket":
        from .feishu_ws import FeishuWebSocketEventSource
        return FeishuWebSocketEventSource(spec, event_bus)
    raise ValueError(f"unknown event source type: {spec.type}")


def create_event_source_manager(specs: list[EventSourceSpec], event_bus: EventBus) -> EventSourceManager:
    return EventSourceManager([
        create_event_source(spec, event_bus)
        for spec in specs
        if spec.enabled
    ])
