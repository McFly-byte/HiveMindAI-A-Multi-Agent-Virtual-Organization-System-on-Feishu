from datetime import datetime

from pydantic import BaseModel, Field

from agent_runtime.base_refs import OutputRecordRef, RecordReadSet, RecordWriteSet
from agent_runtime.enums import AgentName, AgentRunStatus, ErrorType, TriggerType
from agent_runtime.session import AgentSession


class AgentRunRecord(BaseModel):
    """Serializable representation of one AgentRuns table row."""

    run_id: str
    parent_run_id: str | None = None
    session_id: str
    trace_id: str | None = None
    project_id: str
    agent_name: AgentName
    trigger_type: TriggerType
    input_tables: list[str] = Field(default_factory=list)
    input_record_ids: list[str] = Field(default_factory=list)
    output_records: list[OutputRecordRef] = Field(default_factory=list)
    read_sets: list[RecordReadSet] = Field(default_factory=list)
    write_sets: list[RecordWriteSet] = Field(default_factory=list)
    executed_actions: list[str] = Field(default_factory=list)
    reasoning_summary: str | None = None
    evidence_summary: str | None = None
    status: AgentRunStatus
    error_type: ErrorType = ErrorType.NONE
    error_message: str | None = None
    started_at: datetime
    ended_at: datetime | None = None

    @classmethod
    def from_session(cls, session: AgentSession) -> "AgentRunRecord":
        first_error = session.errors[0] if session.errors else None
        return cls(
            run_id=session.run_id,
            parent_run_id=session.parent_run_id,
            session_id=session.session_id,
            trace_id=session.trace_id,
            project_id=session.project_id,
            agent_name=session.agent_name,
            trigger_type=session.trigger_type,
            input_record_ids=session.input_record_ids,
            output_records=session.output_records,
            read_sets=session.read_sets,
            write_sets=session.write_sets,
            reasoning_summary=session.final_summary,
            status=session.status,
            error_type=first_error.error_type if first_error else ErrorType.NONE,
            error_message=first_error.message if first_error else None,
            started_at=session.started_at,
            ended_at=session.ended_at,
        )
