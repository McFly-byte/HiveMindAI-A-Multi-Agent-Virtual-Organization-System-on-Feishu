from __future__ import annotations

from typing import Any

from agent_hive.memory.manager import MemoryManager


class MemoryProvider:
    provider_name = "memory"

    def __init__(self, memory_manager: MemoryManager) -> None:
        self.memory_manager = memory_manager

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        name = tool_name.replace(".", "_")
        if name == "memory_search":
            return {
                "results": await self.memory_manager.search(
                    query=arguments["query"],
                    agent_id=arguments["agent_id"],
                    project_id=arguments.get("project_id"),
                    run_id=arguments.get("run_id"),
                    top_k=arguments.get("top_k", 8),
                    scopes=arguments.get("scopes"),
                    memory_type=arguments.get("memory_type", "all"),
                )
            }
        if name == "memory_write":
            return await self.memory_manager.write(**arguments)
        if name == "memory_reflect":
            return await self.memory_manager.reflect(**arguments)
        raise KeyError(f"unknown memory tool: {tool_name}")
