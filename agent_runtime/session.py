from datetime import datetime
from uuid import uuid4
from pydantic import BaseModel, Field
from schemas.base import utc_now
from agent_runtime.result import ToolResult


class AgentSession(BaseModel):
    """Per-run isolated state. Critical business state must still be written to Feishu Base."""
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str | None = None
    agent_name: str
    trigger_type: str = "手动"
    start_time: datetime = Field(default_factory=utc_now)
    end_time: datetime | None = None
    max_steps: int = 5
    input_tables: list[str] = Field(default_factory=list)
    input_record_ids: list[str] = Field(default_factory=list)
    output_tables: list[str] = Field(default_factory=list)
    output_record_ids: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    rule_results: list[str] = Field(default_factory=list)
    llm_inputs_summary: list[str] = Field(default_factory=list)
    llm_outputs: list[dict] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    status: str = "running"

    def finish(self, status: str = "success") -> None:
        """Mark the session as finished."""
        self.status = status
        self.end_time = utc_now()
