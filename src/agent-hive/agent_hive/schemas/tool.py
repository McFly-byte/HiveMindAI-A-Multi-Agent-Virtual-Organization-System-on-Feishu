from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class ToolIntent(BaseModel):
    """Business intent sent to a delegated tool agent.

    Business agents are allowed to create this model for Feishu work, but they
    are not allowed to select low-level Feishu tool names.
    """

    intent_id: str = Field(default_factory=lambda: f"itn_{uuid4().hex[:12]}")
    domain: str
    action: str
    target: dict[str, Any] = Field(default_factory=dict)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)
    requested_by_agent_id: str | None = None
    target_agent_id: str | None = None
    project_id: str | None = None
    run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ToolStep(BaseModel):
    step_id: str = Field(default_factory=lambda: f"tls_{uuid4().hex[:12]}")
    provider: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    purpose: str = ""
    output_key: str | None = None
    idempotency_key: str | None = None


class ToolPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: f"tlp_{uuid4().hex[:12]}")
    intent_id: str
    domain: str
    action: str
    steps: list[ToolStep] = Field(default_factory=list)
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    ok: bool
    intent_id: str | None = None
    plan_id: str | None = None
    domain: str | None = None
    action: str | None = None
    summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    step_results: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
