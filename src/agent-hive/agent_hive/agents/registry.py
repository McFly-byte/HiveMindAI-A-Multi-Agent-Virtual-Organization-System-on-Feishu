from __future__ import annotations

from agent_hive.agents.base import BaseAgent
from agent_hive.agents.factory import build_agent
from agent_hive.config.models import AgentConfig, RuntimeConfig
from agent_hive.memory.manager import MemoryManager
from agent_hive.tools.executor import ToolExecutor
from agent_hive.tools.providers.llm import LLMProvider


class AgentRegistry:
    def __init__(
        self,
        runtime_config: RuntimeConfig,
        *,
        memory_manager: MemoryManager | None = None,
        tool_executor: ToolExecutor | None = None,
        llm_provider: LLMProvider | None = None,
    ) -> None:
        self.runtime_config = runtime_config
        self.memory_manager = memory_manager
        self.tool_executor = tool_executor
        self.llm_provider = llm_provider
        self._agents: dict[str, BaseAgent] = {}

    def get_config(self, agent_id: str) -> AgentConfig:
        return self.runtime_config.get_agent(agent_id)

    def get_agent(self, agent_id: str) -> BaseAgent:
        if agent_id not in self._agents:
            self._agents[agent_id] = build_agent(
                self.get_config(agent_id),
                memory_manager=self.memory_manager,
                tool_executor=self.tool_executor,
                llm_provider=self.llm_provider,
            )
        return self._agents[agent_id]

    def list_agent_ids(self) -> list[str]:
        return sorted(self.runtime_config.agents)
