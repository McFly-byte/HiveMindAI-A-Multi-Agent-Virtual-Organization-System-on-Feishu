from __future__ import annotations

import asyncio
from pathlib import Path

from agent_hive.agents.registry import AgentRegistry
from agent_hive.config.loader import load_runtime_config
from agent_hive.context.manager import AgentContextManager
from agent_hive.events.models import HiveEvent
from agent_hive.memory.manager import MemoryManager
from agent_hive.runtime.agent_runner import AgentRunner


ROOT = Path(__file__).resolve().parents[2]


def test_llm_agent_loop_executes_memory_tool_calls(tmp_path: Path) -> None:
    async def _run() -> None:
        config = load_runtime_config(ROOT).model_copy(update={"memory_db_path": tmp_path / "memory.db"})
        memory = MemoryManager(tmp_path / "memory.db")
        registry = AgentRegistry(config, memory_manager=memory)
        runner = AgentRunner(
            agent_registry=registry,
            context_manager=AgentContextManager(memory),
            memory_manager=memory,
        )
        event = HiveEvent(
            event_type="manual.loop",
            project_id="p1",
            target_agent_id="risk_analysis",
            payload={
                "loop_decisions": [
                    {
                        "decision": "continue",
                        "thought": "write a useful run memory first",
                        "tool_calls": [
                            {
                                "call_type": "memory",
                                "tool_name": "memory.write",
                                "arguments": {
                                    "content": "loop memory tool call works",
                                    "memory_type": "episodic",
                                    "tags": ["loop-test"],
                                },
                            }
                        ],
                        "summary": "memory written",
                    },
                    {
                        "decision": "finish",
                        "thought": "read the memory back",
                        "tool_calls": [
                            {
                                "call_type": "memory",
                                "tool_name": "memory.search",
                                "arguments": {"query": "loop memory tool call", "top_k": 3},
                            }
                        ],
                        "summary": "loop done",
                        "final_payload": {"ok": True},
                    },
                ]
            },
        )
        try:
            session, output = await runner.run(agent_id="risk_analysis", event=event)
        finally:
            await memory.close()

        assert session.status == "success"
        assert output.loop_iterations == 2
        assert output.summary == "loop done"
        assert [step.kind for step in session.steps] == [
            "observe",
            "think",
            "act",
            "verify",
            "think",
            "act",
            "verify",
            "log",
        ]
        assert any(result.ok and result.data.get("results") for result in output.tool_results)

    asyncio.run(_run())
