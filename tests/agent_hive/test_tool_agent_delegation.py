from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent_hive.agents.registry import AgentRegistry
from agent_hive.config.loader import load_runtime_config
from agent_hive.context.manager import AgentContext, AgentContextManager
from agent_hive.events.models import HiveEvent
from agent_hive.memory.manager import MemoryManager
from agent_hive.runtime.agent_runner import AgentRunner
from agent_hive.runtime.hive_runtime import HiveRuntime
from agent_hive.runtime.session import AgentSession
from agent_hive.schemas.tool import ToolIntent
from agent_hive.tools.executor import ToolExecutor
from agent_hive.tools.providers.memory import MemoryProvider
from agent_hive.tools.providers.trace import TraceProvider
from agent_hive.tools.registry import ProviderRegistry


ROOT = Path(__file__).resolve().parents[2]


class FakeFeishuProvider:
    provider_name = "feishu"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.created_field: dict[str, Any] | None = None

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        if tool_name == "feishu_bitable_list_tables":
            return {"items": [{"name": "A", "table_id": "tbl_A"}], "has_more": False, "page_token": ""}
        if tool_name == "feishu_bitable_list_fields":
            items = [self.created_field] if self.created_field else []
            return {"items": items, "has_more": False, "page_token": ""}
        if tool_name == "feishu_bitable_create_field":
            self.created_field = {
                "field_id": "fld_risk",
                "field_name": arguments["field_name"],
                "field_type": arguments["field_type"],
                "property": arguments.get("property") or {},
            }
            return self.created_field
        raise AssertionError(f"unexpected tool: {tool_name}")


def test_tool_agent_resolves_business_add_field_intent(tmp_path: Path) -> None:
    async def _run() -> None:
        config = load_runtime_config(ROOT).model_copy(update={"memory_db_path": tmp_path / "memory.db"})
        fake_feishu = FakeFeishuProvider()
        providers = ProviderRegistry()
        providers.register(fake_feishu)
        executor = ToolExecutor(providers)
        agent = AgentRegistry(config, tool_executor=executor).get_agent("feishu_tool_agent")
        event = HiveEvent(event_type="tool.feishu.requested", project_id="p1", payload={"app_token": "app_1"})
        session = AgentSession(project_id="p1", agent_id="feishu_tool_agent", input_event_id=event.event_id)
        context = AgentContext(session=session, agent_config=config.agents["feishu_tool_agent"], event=event)
        intent = ToolIntent(
            domain="feishu.bitable",
            action="add_field",
            target={"table_name": "A"},
            arguments={"field_name": "风险等级", "field_type_hint": "single_select", "options": ["低", "中", "高"]},
            constraints={"verify_after_write": True},
            requested_by_agent_id="risk_analysis",
        )

        output = await agent.run(context, {"intent": intent.model_dump(mode="json")})

        assert output.tool_results[0].ok is True
        assert output.tool_results[0].data["created"] is True
        assert [name for name, _ in fake_feishu.calls] == [
            "feishu_bitable_list_tables",
            "feishu_bitable_list_fields",
            "feishu_bitable_create_field",
            "feishu_bitable_list_fields",
        ]

    asyncio.run(_run())


def test_hive_runtime_routes_business_feishu_intent_to_tool_agent(tmp_path: Path) -> None:
    async def _run() -> None:
        config = load_runtime_config(ROOT).model_copy(update={"memory_db_path": tmp_path / "memory.db"})
        memory = MemoryManager(tmp_path / "memory.db")
        fake_feishu = FakeFeishuProvider()
        providers = ProviderRegistry()
        providers.register(MemoryProvider(memory))
        providers.register(fake_feishu)
        providers.register(TraceProvider())
        executor = ToolExecutor(providers)
        registry = AgentRegistry(config, memory_manager=memory, tool_executor=executor)
        context_manager = AgentContextManager(memory)
        runner = AgentRunner(agent_registry=registry, context_manager=context_manager, memory_manager=memory)
        runtime = HiveRuntime(
            runtime_config=config,
            memory_manager=memory,
            providers=providers,
            agent_registry=registry,
            runner=runner,
        )
        try:
            event = HiveEvent(
                event_type="risk.analysis.requested",
                project_id="p1",
                target_agent_id="risk_analysis",
                payload={
                    "summary": "需要新增风险等级字段",
                    "feishu_intent": {
                        "domain": "feishu.bitable",
                        "action": "add_field",
                        "target": {"table_name": "A"},
                        "arguments": {
                            "app_token": "app_1",
                            "field_name": "风险等级",
                            "field_type_hint": "single_select",
                            "options": ["低", "中", "高"],
                        },
                        "constraints": {"verify_after_write": True},
                    },
                },
            )

            result = await runtime.dispatch(event)

            assert result.root_session.agent_id == "risk_analysis"
            assert result.child_sessions[0].agent_id == "feishu_tool_agent"
            assert result.child_outputs[0].tool_results[0].ok is True
        finally:
            await runtime.shutdown()

    asyncio.run(_run())
