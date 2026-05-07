from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class HiveEvent(BaseModel):
    """Runtime event envelope before agent-specific context is assembled."""

    event_id: str = Field(default_factory=lambda: f"evt_{uuid4().hex[:12]}")
    event_type: str
    project_id: str
    source: str = "manual"
    target_agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
