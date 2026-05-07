from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MemoryQuery(BaseModel):
    query: str
    agent_id: str
    project_id: str | None = None
    run_id: str | None = None
    top_k: int = 8
    scopes: list[str] = Field(default_factory=lambda: ["self", "project"])


class MemoryWrite(BaseModel):
    content: str
    agent_id: str
    memory_type: str = "episodic"
    project_id: str | None = None
    run_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
