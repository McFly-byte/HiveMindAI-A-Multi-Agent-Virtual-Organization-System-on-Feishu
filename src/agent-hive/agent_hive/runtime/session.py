from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class StepRecord(BaseModel):
    step_id: str = Field(default_factory=lambda: f"stp_{uuid4().hex[:12]}")
    kind: str
    name: str
    input_summary: str = ""
    output_summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None

    def finish(self, summary: str = "", metadata: dict[str, Any] | None = None) -> None:
        self.output_summary = summary
        if metadata:
            self.metadata.update(metadata)
        self.ended_at = datetime.utcnow()


class AgentSession(BaseModel):
    session_id: str = Field(default_factory=lambda: f"ses_{uuid4().hex[:12]}")
    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex[:12]}")
    parent_run_id: str | None = None
    trace_id: str = Field(default_factory=lambda: f"trc_{uuid4().hex[:12]}")
    project_id: str
    agent_id: str
    status: str = "created"
    input_event_id: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    steps: list[StepRecord] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    final_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None

    def add_step(self, kind: str, name: str, input_summary: str = "") -> StepRecord:
        step = StepRecord(kind=kind, name=name, input_summary=input_summary)
        self.steps.append(step)
        return step

    def add_output(self, output: dict[str, Any]) -> None:
        self.outputs.append(output)

    def mark_running(self) -> None:
        self.status = "running"

    def mark_success(self, summary: str | None = None) -> None:
        self.status = "success"
        self.final_summary = summary
        self.ended_at = datetime.utcnow()

    def mark_failed(self, message: str) -> None:
        self.status = "failed"
        self.errors.append(message)
        self.final_summary = message
        self.ended_at = datetime.utcnow()
