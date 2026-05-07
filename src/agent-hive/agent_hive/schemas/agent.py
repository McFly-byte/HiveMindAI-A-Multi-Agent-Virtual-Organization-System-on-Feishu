from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel, Field

from agent_hive.schemas.orchestration import OrchestrationAction
from agent_hive.schemas.tool import ToolIntent, ToolResult


class AgentLoopToolCall(BaseModel):
    """One tool request selected by an agent loop.

    Business agents may execute ``memory`` calls directly. Feishu calls must be
    expressed as ``feishu_intent`` and delegated by the runtime.
    """

    call_type: Literal["memory", "feishu_intent"] = "memory"
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    intent: ToolIntent | None = None
    reason: str = ""
    output_key: str | None = None


class AgentLoopDecision(BaseModel):
    decision: Literal["continue", "finish", "blocked"] = "finish"
    thought: str = ""
    tool_calls: list[AgentLoopToolCall] = Field(default_factory=list)
    summary: str = ""
    final_payload: dict[str, Any] = Field(default_factory=dict)
    blocked_reason: str | None = None


class AgentOutput(BaseModel):
    agent_id: str
    run_id: str
    status: str = "success"
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    orchestration_actions: list[OrchestrationAction] = Field(default_factory=list)
    tool_intents: list[ToolIntent] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    loop_iterations: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
