from __future__ import annotations

from agent_hive.config.models import AgentConfig, RuntimeConfig


class ConfigValidationError(ValueError):
    pass


def validate_runtime_config(config: RuntimeConfig) -> None:
    agent_ids = set(config.agents)
    for agent in config.agents.values():
        validate_agent_config(agent, agent_ids)


def validate_agent_config(config: AgentConfig, known_agent_ids: set[str] | None = None) -> None:
    if config.role == "business_agent":
        delegate = config.tool_access.feishu_delegate()
        if delegate is None:
            raise ConfigValidationError(
                f"business agent {config.agent_id!r} must delegate feishu to feishu_tool_agent"
            )
        if known_agent_ids is not None and delegate not in known_agent_ids:
            raise ConfigValidationError(
                f"business agent {config.agent_id!r} delegates feishu to unknown agent {delegate!r}"
            )
    if config.role == "tool_agent":
        if not config.tool_agent.domain:
            raise ConfigValidationError(f"tool agent {config.agent_id!r} must declare tool_agent.domain")
