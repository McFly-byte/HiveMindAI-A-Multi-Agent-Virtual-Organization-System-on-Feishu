from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agent_runtime.base_refs import BaseRecordRef
from agent_runtime.enums import AgentName, EventType, TriggerType


class EventScope(BaseModel):
    """Scope constraints for one runtime trigger."""

    tables: list[str] = Field(default_factory=list)
    time_range: str | None = None
    record_ids: list[str] = Field(default_factory=list)
    include_history: bool = True
    include_agent_runs: bool = False


class AgentTriggerEvent(BaseModel):
    """Normalized event that starts an agent run."""

    event_id: str
    event_type: EventType
    trigger_type: TriggerType
    project_id: str
    target_agent: AgentName | None = None
    trigger_user: str | None = None
    user_intent: str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    input_records: list[BaseRecordRef] = Field(default_factory=list)
    scope: EventScope = Field(default_factory=EventScope)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentCallRequest(BaseModel):
    """Request for the coordinator/runtime to invoke one agent."""

    parent_run_id: str | None = None
    agent_name: AgentName
    event: AgentTriggerEvent
    reason: str
    input_payload_ref: str | None = None
