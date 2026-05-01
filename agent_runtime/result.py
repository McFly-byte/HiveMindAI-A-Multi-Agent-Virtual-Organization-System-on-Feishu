from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """Unified return value for every Tool."""
    tool_name: str
    success: bool
    inputs_summary: str = ""
    outputs_summary: str = ""
    output_record_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    retry_count: int = 0


class AgentResult(BaseModel):
    """Structured Agent run result returned by Gateway."""
    run_id: str
    agent_name: str
    status: str
    message: str
    output_record_ids: list[str] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
