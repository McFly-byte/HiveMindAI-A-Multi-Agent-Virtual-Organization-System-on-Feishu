from __future__ import annotations

from typing import Any

from agent_hive.config.models import AgentConfig
from agent_hive.context.manager import AgentContext
from agent_hive.memory.manager import MemoryManager
from agent_hive.schemas.agent import AgentOutput
from agent_hive.tools.executor import ToolExecutor
from agent_hive.tools.providers.llm import LLMProvider


class BaseAgent:
    def __init__(
        self,
        config: AgentConfig,
        *,
        memory_manager: MemoryManager | None = None,
        llm_provider: LLMProvider | None = None,
        tool_executor: ToolExecutor | None = None,
    ) -> None:
        self.config = config
        self.memory_manager = memory_manager
        self.llm_provider = llm_provider
        self.tool_executor = tool_executor

    async def run(self, context: AgentContext, payload: dict[str, Any]) -> AgentOutput:
        raise NotImplementedError
