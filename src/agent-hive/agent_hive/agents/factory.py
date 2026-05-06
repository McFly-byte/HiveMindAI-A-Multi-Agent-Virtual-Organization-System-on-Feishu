from __future__ import annotations

from agent_hive.agents.base import BaseAgent
from agent_hive.agents.llm_agent import LLMAgent
from agent_hive.agents.orchestrator import OrchestratorAgent
from agent_hive.agents.tool_agent import ToolAgent
from agent_hive.config.models import AgentConfig
from agent_hive.memory.manager import MemoryManager
from agent_hive.tools.executor import ToolExecutor
from agent_hive.tools.planner import ToolPlanner
from agent_hive.tools.providers.llm import LLMProvider


def build_agent(
    config: AgentConfig,
    *,
    memory_manager: MemoryManager | None = None,
    tool_executor: ToolExecutor | None = None,
    llm_provider: LLMProvider | None = None,
) -> BaseAgent:
    if config.entrypoint in {"orchestrator", "orchestrator_agent"}:
        return OrchestratorAgent(config, memory_manager=memory_manager, llm_provider=llm_provider)
    if config.entrypoint == "tool_agent":
        return ToolAgent(config, memory_manager=memory_manager, tool_executor=tool_executor, planner=ToolPlanner())
    if config.entrypoint == "llm_agent":
        return LLMAgent(config, memory_manager=memory_manager, llm_provider=llm_provider)
    raise ValueError(f"unknown agent entrypoint: {config.entrypoint}")
