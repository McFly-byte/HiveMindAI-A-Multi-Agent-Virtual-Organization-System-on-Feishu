from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_runtime.enums import AgentName, AgentRole, ModelProvider, ToolPermission


class PromptConfig(BaseModel):
    """Prompt file locations and output contract for an agent."""

    agent_md_path: Path
    shared_prompt_paths: list[Path] = Field(default_factory=list)
    output_schema_name: str
    prompt_version: str = "v1"


class ModelConfig(BaseModel):
    """LLM configuration metadata; no client is created here."""

    provider: ModelProvider
    model_name: str
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
    """Bounded runtime limits for MVP agent execution."""

    max_steps: int = 5
    max_tool_calls: int = 20
    max_llm_calls: int = 3
    timeout_seconds: int = 180
    max_retries: int = 2

    @field_validator("max_steps")
    @classmethod
    def validate_max_steps(cls, value: int) -> int:
        if value <= 0 or value > 10:
            raise ValueError("max_steps must be between 1 and 10")
        return value


class ToolPolicy(BaseModel):
    """Tool access policy declared for an agent."""

    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    default_permission: ToolPermission = ToolPermission.READ_ONLY
    write_tools_require_quality_gate: bool = True


class WritePolicy(BaseModel):
    """Base write policy; business agents default to read-only."""

    can_write_base: bool = False
    writable_tables: list[str] = Field(default_factory=list)
    require_quality_gate: bool = True
    require_idempotency_key: bool = True
    allow_direct_message_send: bool = False

    @model_validator(mode="after")
    def validate_quality_gate_for_writes(self) -> "WritePolicy":
        if self.can_write_base and not self.require_quality_gate:
            raise ValueError("Base writes must require quality gate")
        return self


class HookPolicy(BaseModel):
    """Named lifecycle hooks declared by configuration only."""

    pre_act_hooks: list[str] = Field(default_factory=list)
    post_act_hooks: list[str] = Field(default_factory=list)
    pre_write_hooks: list[str] = Field(default_factory=list)
    post_write_hooks: list[str] = Field(default_factory=list)
    final_verify_hooks: list[str] = Field(default_factory=list)


class AgentConfig(BaseModel):
    """Full runtime configuration loaded from one agent.yaml."""

    agent_name: AgentName
    role: AgentRole
    display_name: str
    description: str
    workspace_path: Path
    prompt: PromptConfig
    model: ModelConfig
    runtime_limits: RuntimeLimits = Field(default_factory=RuntimeLimits)
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)
    write_policy: WritePolicy = Field(default_factory=WritePolicy)
    hook_policy: HookPolicy = Field(default_factory=HookPolicy)
    input_schema: str
    output_schema: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeConfig(BaseModel):
    """Runtime-level configuration assembled from all agent configs."""

    app_name: str = "HiveMindAI"
    environment: str = "dev"
    agents: dict[AgentName, AgentConfig]
    default_trace_enabled: bool = True
    langsmith_enabled: bool = False
    local_trace_dir: Path = Path("traces")
    session_ttl_hours: int = 24
