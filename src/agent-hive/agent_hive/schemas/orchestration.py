from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class OrchestrationAction(BaseModel):
    action_id: str = Field(default_factory=lambda: f"orc_{uuid4().hex[:12]}")
    action_type: str
    target_agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class OrchestrationPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: f"orp_{uuid4().hex[:12]}")
    root_agent_id: str
    actions: list[OrchestrationAction] = Field(default_factory=list)
    stop_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
