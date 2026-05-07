from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class PromptConfig(BaseModel):
    """Prompt files for one agent."""

    system: Path
    shared: list[Path] = Field(default_factory=list)


class ModelConfig(BaseModel):
    """LLM configuration metadata. Clients are created by providers."""

    provider: str = "deepseek"
    name: str = "deepseek-chat"
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout_seconds: int = 60
    json_mode: bool = True

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, value: float) -> float:
        if value < 0 or value > 2:
            raise ValueError("temperature must be between 0 and 2")
        return value


class RuntimeLimits(BaseModel):
    max_steps: int = 5
    max_child_runs: int = 8
    timeout_seconds: int = 180
    max_retries: int = 2

    @field_validator("max_steps", "max_child_runs")
    @classmethod
    def validate_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("runtime limits must be positive")
        return value


class MemoryPolicy(BaseModel):
    """Per-agent memory access policy.

    Business agents may use memory directly; Feishu remains delegated.
    """

    enabled: bool = True
    namespace: str = "self"
    read_scopes: list[str] = Field(default_factory=lambda: ["self", "project"])
    write_types: list[str] = Field(default_factory=lambda: ["episodic"])
    max_search_results: int = 8
    auto_write_run_summary: bool = True


class ToolAccessPolicy(BaseModel):
    """Direct and delegated tool access boundary.

    ``direct_toolsets`` is intentionally expected to contain ``memory`` for
    business agents. ``feishu`` must be delegated to ``feishu_tool_agent``.
    """

    direct_toolsets: list[str] = Field(default_factory=list)
    direct_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    delegated_toolsets: dict[str, str] = Field(default_factory=dict)

    def can_call_memory_directly(self) -> bool:
        return "memory" in self.direct_toolsets or any(tool.startswith("memory.") for tool in self.direct_tools)

    def feishu_delegate(self) -> str | None:
        return self.delegated_toolsets.get("feishu")


class OrchestrationPolicy(BaseModel):
    subscribes: list[str] = Field(default_factory=list)
    emits: list[str] = Field(default_factory=list)
    priority: int = 0
    max_parallel_runs: int = 1


class ToolAgentConfig(BaseModel):
    domain: str | None = None
    providers: list[str] = Field(default_factory=list)


class AgentConfig(BaseModel):
    """Configuration loaded from ``agents/*/agent.yaml``.

    The schema is new-runtime only and deliberately avoids legacy enums.
    """

    agent_id: str
    role: str
    entrypoint: str
    display_name: str
    description: str = ""
    workspace_path: Path
    prompt: PromptConfig
    model: ModelConfig | None = None
    runtime_limits: RuntimeLimits = Field(default_factory=RuntimeLimits)
    memory: MemoryPolicy = Field(default_factory=MemoryPolicy)
    tool_access: ToolAccessPolicy = Field(default_factory=ToolAccessPolicy)
    orchestration: OrchestrationPolicy = Field(default_factory=OrchestrationPolicy)
    tool_agent: ToolAgentConfig = Field(default_factory=ToolAgentConfig)
    input_schema: str = ""
    output_schema: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("agent_id", "role", "entrypoint")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_business_tool_boundary(self) -> "AgentConfig":
        if self.role == "business_agent" and "feishu" in self.tool_access.direct_toolsets:
            raise ValueError("business agents cannot call feishu directly; delegate to feishu_tool_agent")
        return self


class RuntimeConfig(BaseModel):
    app_name: str = "HiveMindAI"
    environment: str = "dev"
    project_root: Path
    agents_root: Path
    runtime_dir: Path = Path("runtime")
    memory_db_path: Path = Path("runtime/memory.db")
    trace_dir: Path = Path("runtime/traces")
    run_dir: Path = Path("runtime/runs")
    agents: dict[str, AgentConfig]

    def get_agent(self, agent_id: str) -> AgentConfig:
        try:
            return self.agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"unknown agent: {agent_id}") from exc
