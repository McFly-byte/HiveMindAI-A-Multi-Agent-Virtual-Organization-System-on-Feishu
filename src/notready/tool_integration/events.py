"""Tool-layer events for tool-runtime telemetry.

The agent runtime models why a run started; this module models what happened inside tool execution.
Here we model *what happened inside tool execution*, using the same idioms:

- Pydantic :class:`~pydantic.BaseModel`, :class:`~pydantic.Field`, ``datetime.utcnow``
- Typed ``scope`` (parallel to ``EventScope``)
- Strong ``event_type`` via :class:`ToolIntegrationEventType` plus ``str`` for domain emits

Transport is still pub/sub (:class:`EventBus`); subscriptions match on ``event_type`` string values."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from queue import Empty, Queue
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolIntegrationEventType(StrEnum):
    """Standard tool-runtime telemetry types (parallel role to agent ``EventType``)."""

    TOOL_CALL_STARTED = "tool.call.started"
    TOOL_CALL_FINISHED = "tool.call.finished"
    TOOL_CALL_FAILED = "tool.call.failed"
    TOOL_JOB_STARTED = "tool.job.started"
    TOOL_JOB_FOREGROUND_WAIT_STARTED = "tool.job.foreground_wait_started"
    TOOL_JOB_SWITCHED_TO_BACKGROUND = "tool.job.switched_to_background"
    TOOL_JOB_FINISHED = "tool.job.finished"
    TOOL_JOB_FAILED = "tool.job.failed"
    TOOL_JOB_CANCELLED = "tool.job.cancelled"
    TOOL_JOB_CHECKPOINT = "tool.job.checkpoint"


class ToolEventScope(BaseModel):
    """Optional linkage from tool telemetry back to an agent run (similar idea to ``EventScope``).

    Populate from ``ToolContext.config`` or executor when wiring to ``AgentRuntime``."""

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None


class ToolIntegrationEvent(BaseModel):
    """One publishable unit on the tool integration event bus.

    Carries standard event envelope fields:
    typed ``event_id``, ``timestamp``, ``scope``, and a payload bag."""

    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    event_type: ToolIntegrationEventType | str = Field(..., description="Standard or domain event name.")
    source: str = Field(..., description="Emitting component, usually tool name.")
    payload: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = None
    job_id: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    scope: ToolEventScope | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _event_type_key(ev: ToolIntegrationEvent) -> str:
    et = ev.event_type
    return et.value if isinstance(et, ToolIntegrationEventType) else str(et)


class EventBus:
    """In-process pub/sub event bus."""

    def __init__(self):
        self._subscribers: dict[str, dict[str, Any]] = {}
        self._events: list[ToolIntegrationEvent] = []

    def subscribe(self, subscriber_id: str, event_types: list[str]):
        self._subscribers[subscriber_id] = {
            "event_types": set(event_types),
            "queue": Queue(),
        }

    def publish(self, event: ToolIntegrationEvent):
        self._events.append(event)
        et = _event_type_key(event)
        for sub in self._subscribers.values():
            event_types: set[str] = sub["event_types"]
            if "*" in event_types or et in event_types:
                sub["queue"].put(event)

    def drain(self, subscriber_id: str) -> list[ToolIntegrationEvent]:
        sub = self._subscribers.get(subscriber_id)
        if not sub:
            return []
        queue: Queue = sub["queue"]
        events: list[ToolIntegrationEvent] = []
        while True:
            try:
                events.append(queue.get_nowait())
            except Empty:
                return events

    def recent(self, limit: int = 20) -> list[ToolIntegrationEvent]:
        return self._events[-limit:]

    def subscriber_queue_sizes(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for sub_id, sub in self._subscribers.items():
            queue: Queue = sub["queue"]
            out[sub_id] = queue.qsize()
        return out

    def pending_for(self, subscriber_id: str) -> list[ToolIntegrationEvent]:
        sub = self._subscribers.get(subscriber_id)
        if not sub:
            return []
        queue: Queue = sub["queue"]
        with queue.mutex:
            return list(queue.queue)


# Backward-compatible name for callers that used the old dataclass.
Event = ToolIntegrationEvent

