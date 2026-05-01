from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return timezone-aware UTC time."""
    return datetime.now(timezone.utc)


class BaseRecord(BaseModel):
    """Generic Feishu Base record wrapper."""
    record_id: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


class RunRequest(BaseModel):
    """Request body for manually triggering an Agent."""
    project_id: str | None = None
    trigger_type: str = "手动"
    input_record_ids: list[str] = Field(default_factory=list)
