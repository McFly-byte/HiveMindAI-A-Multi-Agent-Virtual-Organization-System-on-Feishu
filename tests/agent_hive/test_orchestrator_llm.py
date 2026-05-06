from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent_hive.agents.registry import AgentRegistry
from agent_hive.config.loader import load_runtime_config
from agent_hive.context.manager import AgentContextManager
from agent_hive.events.models import HiveEvent
from agent_hive.memory.manager import MemoryManager
from agent_hive.runtime.agent_runner import AgentRunner
from agent_hive.runtime.hive_runtime import HiveRuntime
from agent_hive.tools.executor import ToolExecutor
from agent_hive.tools.providers.memory import MemoryProvider
from agent_hive.tools.providers.trace import TraceProvider
from agent_hive.tools.registry import ProviderRegistry
from agent_hive.agents.orchestrator import _coerce_tool_intents


ROOT = Path(__file__).resolve().parents[2]


class FakeLLMProvider:
    provider_name = "llm"

    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    async def generate_json(self, *, model_config, messages: list[dict[str, str]]) -> dict[str, Any]:
        self.calls.append(messages)
        return {
            "thought": "route risk wording to risk_analysis",
            "summary": "routed message to risk_analysis",
            "actions": [
                {
                    "action_type": "run_agent",
                    "target_agent_id": "risk_analysis",
                    "payload": {"summary": "analyze the incoming risk message", "use_llm": False},
                    "reason": "message asks about risk",
                }
            ],
        }


def test_orchestrator_uses_llm_decision_to_dispatch_child_agent(tmp_path: Path) -> None:
    async def _run() -> None:
        config = load_runtime_config(ROOT).model_copy(update={"memory_db_path": tmp_path / "memory.db"})
        memory = MemoryManager(tmp_path / "memory.db")
        providers = ProviderRegistry()
        providers.register(MemoryProvider(memory))
        providers.register(TraceProvider())
        executor = ToolExecutor(providers)
        llm = FakeLLMProvider()
        registry = AgentRegistry(config, memory_manager=memory, tool_executor=executor, llm_provider=llm)  # type: ignore[arg-type]
        runner = AgentRunner(
            agent_registry=registry,
            context_manager=AgentContextManager(memory),
            memory_manager=memory,
        )
        runtime = HiveRuntime(
            runtime_config=config,
            memory_manager=memory,
            providers=providers,
            agent_registry=registry,
            runner=runner,
        )
        try:
            event = HiveEvent(
                event_type="feishu.im.message.received",
                project_id="p1",
                target_agent_id="orchestrator",
                payload={"text": "这个项目有什么风险？", "chat_id": "oc_1", "message_id": "om_1"},
            )

            result = await runtime.dispatch(event)

            assert len(llm.calls) == 1
            assert result.root_session.agent_id == "orchestrator"
            assert result.root_output.orchestration_actions[0].target_agent_id == "risk_analysis"
            assert [session.agent_id for session in result.child_sessions] == ["risk_analysis"]
            assert result.child_outputs[0].summary == "analyze the incoming risk message"
            assert result.child_outputs[0].payload["text"] == "这个项目有什么风险？"
            assert result.child_outputs[0].payload["chat_id"] == "oc_1"
        finally:
            await runtime.shutdown()

    asyncio.run(_run())


def test_orchestrator_accepts_wrapped_feishu_intent_from_llm() -> None:
    intents = _coerce_tool_intents(
        {
            "tool_intents": [
                {
                    "call_type": "feishu_intent",
                    "reason": "delegate inspection",
                    "intent": {
                        "domain": "feishu.bitable",
                        "action": "inspect_data_gaps",
                        "target": {"knowledge_space_name": "项目中枢", "base_name": "enterprise_rag表"},
                        "arguments": {"stale_days": 7},
                    },
                }
            ]
        },
        default_requester="orchestrator",
    )

    assert len(intents) == 1
    assert intents[0].domain == "feishu.bitable"
    assert intents[0].action == "inspect_data_gaps"
    assert intents[0].requested_by_agent_id == "orchestrator"
