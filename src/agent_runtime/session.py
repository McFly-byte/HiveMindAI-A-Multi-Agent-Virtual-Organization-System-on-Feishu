from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agent_runtime.base_refs import OutputRecordRef, RecordReadSet, RecordWriteSet
from agent_runtime.enums import AgentName, AgentRunStatus, AgentStepType, ErrorType, TriggerType


class RuntimeErrorInfo(BaseModel):
    """Serializable error summary for one runtime failure."""

    error_type: ErrorType
    message: str
    detail: str | None = None
    retryable: bool = False
    occurred_at: datetime = Field(default_factory=datetime.utcnow)


class ToolCallRecord(BaseModel):
    """Auditable summary of a tool call without sensitive raw payloads."""

    tool_call_id: str
    tool_name: str
    input_summary: str | None = None
    output_summary: str | None = None
    success: bool
    retry_count: int = 0
    error: RuntimeErrorInfo | None = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    read_sets: list[RecordReadSet] = Field(default_factory=list)
    write_sets: list[RecordWriteSet] = Field(default_factory=list)


class LLMCallRecord(BaseModel):
    """Auditable summary of an LLM call without full prompt or response text."""

    llm_call_id: str
    provider: str
    model_name: str
    prompt_summary: str
    output_summary: str | None = None
    output_json_valid: bool = False
    retry_count: int = 0
    error: RuntimeErrorInfo | None = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None


class AgentStepRecord(BaseModel):
    """One observe/think/act/verify/log step in an AgentSession."""

    step_id: str
    step_type: AgentStepType
    step_index: int
    input_summary: str | None = None
    output_summary: str | None = None
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    llm_calls: list[LLMCallRecord] = Field(default_factory=list)
    errors: list[RuntimeErrorInfo] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None


class SessionMemoryItem(BaseModel):
    """Short-lived runtime memory item for the current run only."""

    key: str
    value: Any
    summary: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentSession(BaseModel):
    """Complete short-lived lifecycle state for one agent run."""

    session_id: str
    run_id: str
    parent_run_id: str | None = None
    trace_id: str | None = None
    project_id: str
    agent_name: AgentName
    trigger_type: TriggerType
    trigger_user: str | None = None
    status: AgentRunStatus = AgentRunStatus.CREATED
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    current_step_index: int = 0
    max_steps: int = 5
    retry_count: int = 0
    input_record_ids: list[str] = Field(default_factory=list)
    output_records: list[OutputRecordRef] = Field(default_factory=list)
    read_sets: list[RecordReadSet] = Field(default_factory=list)
    write_sets: list[RecordWriteSet] = Field(default_factory=list)
    steps: list[AgentStepRecord] = Field(default_factory=list)
    memory: list[SessionMemoryItem] = Field(default_factory=list)
    errors: list[RuntimeErrorInfo] = Field(default_factory=list)
    final_output_ref: str | None = None
    final_summary: str | None = None

    def add_step(self, step: AgentStepRecord) -> None:
        self.steps.append(step)
        self.current_step_index = step.step_index

    def add_error(self, error: RuntimeErrorInfo) -> None:
        self.errors.append(error)

    def mark_success(self, summary: str | None = None) -> None:
        self.status = AgentRunStatus.SUCCESS
        self.final_summary = summary
        self.ended_at = datetime.utcnow()

    def mark_failed(self, error: RuntimeErrorInfo) -> None:
        self.status = AgentRunStatus.FAILED
        self.add_error(error)
        self.ended_at = datetime.utcnow()
