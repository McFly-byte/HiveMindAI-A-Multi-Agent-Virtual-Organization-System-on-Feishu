from __future__ import annotations

from agent_hive.config.models import AgentConfig, RuntimeConfig
from agent_hive.events.models import HiveEvent


class EventRouter:
    """Routes events to agents using ``orchestration.subscribes``."""

    def __init__(self, runtime_config: RuntimeConfig) -> None:
        self.runtime_config = runtime_config

    def subscribers_for(self, event: HiveEvent) -> list[AgentConfig]:
        if event.target_agent_id:
            return [self.runtime_config.get_agent(event.target_agent_id)]
        candidates = [
            config
            for config in self.runtime_config.agents.values()
            if event.event_type in config.orchestration.subscribes or "*" in config.orchestration.subscribes
        ]
        return sorted(candidates, key=lambda item: item.orchestration.priority, reverse=True)

    def delegated_agent_for(self, source_agent_id: str, toolset: str) -> AgentConfig:
        source = self.runtime_config.get_agent(source_agent_id)
        target = source.tool_access.delegated_toolsets.get(toolset)
        if not target:
            raise KeyError(f"agent {source_agent_id!r} has no delegated toolset {toolset!r}")
        return self.runtime_config.get_agent(target)
