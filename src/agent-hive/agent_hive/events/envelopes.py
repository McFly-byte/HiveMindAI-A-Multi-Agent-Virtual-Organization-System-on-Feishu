from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, Field

from agent_hive.events.models import HiveEvent


class EventEnvelope(BaseModel):
    """Adds trace/run linkage around a HiveEvent."""

    trace_id: str = Field(default_factory=lambda: f"trc_{uuid4().hex[:12]}")
    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex[:12]}")
    parent_run_id: str | None = None
    source_agent_id: str | None = None
    target_agent_id: str | None = None
    event: HiveEvent
