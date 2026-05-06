from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


AgentActionType = Literal[
    "call_agent",
    "call_tool",
    "writeback",
    "finish",
    "block",
    "noop",
]

AgentLoopDecisionType = Literal["continue", "finish", "blocked"]


class AgentLoopAction(BaseModel):
    """One executable action selected by an agent loop."""

    action_id: str = Field(default_factory=lambda: f"act_{uuid4().hex[:12]}")
    action_type: AgentActionType
    reason: str
    target_agent: str | None = None
    tool_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    output_key: str | None = None
    require_quality_gate: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentLoopDecision(BaseModel):
    """A thought step output that tells the runtime whether the loop continues."""

    decision: AgentLoopDecisionType
    reason: str
    actions: list[AgentLoopAction] = Field(default_factory=list)
    evidence_status: str = "unknown"
    finish_reason: str | None = None
    blocked_reason: str | None = None


class AgentLoopState(BaseModel):
    """Mutable state carried across Observe/Think/Act/Verify iterations."""

    loop_id: str = Field(default_factory=lambda: f"loop_{uuid4().hex[:12]}")
    goal: str
    max_iterations: int
    iteration: int = 0
    observations: list[Any] = Field(default_factory=list)
    thoughts: list[Any] = Field(default_factory=list)
    action_results: list[Any] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    finished: bool = False
    blocked: bool = False
    stop_reason: str | None = None
    final_output: Any | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def record_observation(self, observation: Any) -> None:
        self.observations.append(observation)
        self.updated_at = datetime.utcnow()

    def record_thought(self, thought: Any) -> None:
        self.thoughts.append(thought)
        self.updated_at = datetime.utcnow()

    def record_action_result(self, result: Any) -> None:
        self.action_results.append(result)
        self.updated_at = datetime.utcnow()

    def finish(self, output: Any, reason: str | None = None) -> None:
        self.finished = True
        self.final_output = output
        self.stop_reason = reason
        self.updated_at = datetime.utcnow()

    def block(self, reason: str, output: Any | None = None) -> None:
        self.blocked = True
        self.finished = True
        self.final_output = output
        self.stop_reason = reason
        self.updated_at = datetime.utcnow()

    @property
    def can_continue(self) -> bool:
        return not self.finished and not self.blocked and self.iteration + 1 < self.max_iterations
