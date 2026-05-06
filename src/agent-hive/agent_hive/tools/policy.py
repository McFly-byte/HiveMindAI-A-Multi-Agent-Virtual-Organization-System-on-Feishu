from __future__ import annotations

from agent_hive.config.models import AgentConfig
from agent_hive.schemas.tool import ToolIntent
from agent_hive.tools.namespaces import toolset_for_name


class ToolPolicyError(PermissionError):
    pass


class ToolPolicyEngine:
    """Enforces direct memory and delegated Feishu boundaries."""

    def assert_direct_tool_allowed(self, agent: AgentConfig, tool_name: str) -> None:
        toolset = toolset_for_name(tool_name)
        if any(_matches(pattern, tool_name) for pattern in agent.tool_access.denied_tools):
            raise ToolPolicyError(f"tool {tool_name!r} is denied for agent {agent.agent_id!r}")
        if toolset == "feishu" and agent.role != "tool_agent":
            raise ToolPolicyError("business agents cannot directly call feishu tools")
        if toolset in agent.tool_access.direct_toolsets or tool_name in agent.tool_access.direct_tools:
            return
        raise ToolPolicyError(f"tool {tool_name!r} is not directly allowed for agent {agent.agent_id!r}")

    def delegated_agent_for(self, agent: AgentConfig, intent: ToolIntent) -> str:
        domain = intent.domain.split(".", 1)[0]
        target = agent.tool_access.delegated_toolsets.get(domain)
        if not target:
            raise ToolPolicyError(f"agent {agent.agent_id!r} cannot delegate domain {intent.domain!r}")
        return target


def _matches(pattern: str, value: str) -> bool:
    if pattern.endswith(".*"):
        return value.startswith(pattern[:-1])
    return pattern == value
