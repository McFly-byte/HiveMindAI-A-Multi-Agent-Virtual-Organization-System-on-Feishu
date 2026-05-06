from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent_hive.agents.registry import AgentRegistry
from agent_hive.config.loader import load_runtime_config
from agent_hive.context.manager import AgentContext
from agent_hive.events.models import HiveEvent
from agent_hive.events.sources.schedule import ScheduledEventSource
from agent_hive.memory.manager import MemoryManager
from agent_hive.runtime.session import AgentSession
from agent_hive.schemas.tool import ToolIntent
from agent_hive.tools.executor import ToolExecutor
from agent_hive.tools.registry import ProviderRegistry


ROOT = Path(__file__).resolve().parents[2]


class FakeFR02FeishuProvider:
    provider_name = "feishu"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        if tool_name == "feishu_wiki_list_spaces":
            return {"items": [{"space_id": "spc_1", "name": "项目中枢"}], "has_more": False, "page_token": ""}
        if tool_name == "feishu_wiki_list_nodes":
            return {
                "items": [
                    {
                        "space_id": "spc_1",
                        "node_token": "wik_1",
                        "obj_token": "app_1",
                        "obj_type": "bitable",
                        "title": "enterprise_rag表",
                        "has_child": False,
                    }
                ],
                "has_more": False,
                "page_token": "",
            }
        if tool_name == "feishu_bitable_list_tables":
            return {
                "items": [
                    {"table_id": "tbl_project", "name": "项目表"},
                    {"table_id": "tbl_task", "name": "任务表"},
                    {"table_id": "tbl_milestone", "name": "里程碑表"},
                    {"table_id": "tbl_minutes", "name": "会议纪要表"},
                    {"table_id": "tbl_risk", "name": "风险表"},
                ],
                "has_more": False,
                "page_token": "",
            }
        if tool_name == "feishu_bitable_list_fields":
            return {"items": _fields(arguments["table_id"]), "has_more": False, "page_token": ""}
        if tool_name == "feishu_bitable_search_records":
            return {"items": _records(arguments["table_id"]), "has_more": False, "page_token": ""}
        raise AssertionError(f"unexpected tool: {tool_name}")


class FakeFR02SchemaOnlyFeishuProvider(FakeFR02FeishuProvider):
    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "feishu_bitable_list_tables":
            return {
                "items": [
                    {"table_id": "tbl_1", "name": "数据源 A"},
                    {"table_id": "tbl_2", "name": "数据源 B"},
                    {"table_id": "tbl_3", "name": "数据源 C"},
                ],
                "has_more": False,
                "page_token": "",
            }
        if tool_name == "feishu_bitable_list_fields":
            return {"items": _schema_only_fields(arguments["table_id"]), "has_more": False, "page_token": ""}
        if tool_name == "feishu_bitable_search_records":
            return {"items": _schema_only_records(arguments["table_id"]), "has_more": False, "page_token": ""}
        return await super().call(tool_name, arguments)


def test_fr02_tool_agent_discovers_tables_inspects_gaps_and_writes_memory(tmp_path: Path) -> None:
    async def _run() -> None:
        config = load_runtime_config(ROOT).model_copy(update={"memory_db_path": tmp_path / "memory.db"})
        memory = MemoryManager(tmp_path / "memory.db")
        providers = ProviderRegistry()
        providers.register(FakeFR02FeishuProvider())
        executor = ToolExecutor(providers)
        agent = AgentRegistry(config, memory_manager=memory, tool_executor=executor).get_agent("feishu_tool_agent")
        event = HiveEvent(event_type="fr02.inspection.requested", project_id="p1", payload={})
        session = AgentSession(project_id="p1", agent_id="feishu_tool_agent", input_event_id=event.event_id)
        context = AgentContext(session=session, agent_config=config.agents["feishu_tool_agent"], event=event)
        intent = ToolIntent(
            domain="feishu.bitable",
            action="inspect_data_gaps",
            target={"knowledge_space_name": "项目中枢", "base_name": "enterprise_rag表"},
            arguments={"stale_days": 7},
            constraints={"remember_discovered_resources": True},
            requested_by_agent_id="data_gap_inspector",
        )
        try:
            output = await agent.run(context, {"intent": intent.model_dump(mode="json")})
            result = output.tool_results[0]

            assert result.ok is True
            assert result.data["resource"]["app_token"] == "app_1"
            assert result.data["resource"]["tables"]["task"]["table_id"] == "tbl_task"
            assert len(result.data["findings"]["missing_fields"]) >= 2
            assert result.data["findings"]["overdue_unupdated_tasks"][0]["record_id"] == "rec_task_1"
            assert result.data["findings"]["unsynced_meeting_minutes"][0]["record_id"] == "rec_min_1"

            memories = await memory.search(
                query="feishu_resource_discovery enterprise_rag表",
                agent_id="feishu_tool_agent",
                project_id="p1",
                top_k=5,
            )
            assert memories
        finally:
            await memory.close()

    asyncio.run(_run())


def test_fr02_tool_agent_resolves_unknown_table_names_by_schema(tmp_path: Path) -> None:
    async def _run() -> None:
        config = load_runtime_config(ROOT).model_copy(update={"memory_db_path": tmp_path / "memory.db"})
        memory = MemoryManager(tmp_path / "memory.db")
        providers = ProviderRegistry()
        providers.register(FakeFR02SchemaOnlyFeishuProvider())
        executor = ToolExecutor(providers)
        agent = AgentRegistry(config, memory_manager=memory, tool_executor=executor).get_agent("feishu_tool_agent")
        event = HiveEvent(event_type="fr02.inspection.requested", project_id="p1", payload={})
        session = AgentSession(project_id="p1", agent_id="feishu_tool_agent", input_event_id=event.event_id)
        context = AgentContext(session=session, agent_config=config.agents["feishu_tool_agent"], event=event)
        intent = ToolIntent(
            domain="feishu.bitable",
            action="inspect_data_gaps",
            target={"knowledge_space_name": "项目中枢", "base_name": "enterprise_rag表"},
            arguments={"stale_days": 7},
            constraints={"remember_discovered_resources": False},
            requested_by_agent_id="data_gap_inspector",
        )
        try:
            output = await agent.run(context, {"intent": intent.model_dump(mode="json")})
            tables = output.tool_results[0].data["resource"]["tables"]

            assert tables["task"]["table_id"] == "tbl_1"
            assert tables["milestone"]["table_id"] == "tbl_2"
            assert tables["meeting_minutes"]["table_id"] == "tbl_3"
            assert tables["task"]["_match_method"] == "schema"
        finally:
            await memory.close()

    asyncio.run(_run())


def test_scheduled_event_source_emits_fr02_event() -> None:
    async def _run() -> None:
        emitted = []
        source = ScheduledEventSource(
            name="fr02_inspection",
            event_type="fr02.inspection.requested",
            project_id="p1",
            target_agent_id="orchestrator",
            payload_factory=lambda: {"summary": "FR-02"},
            interval_seconds=3600,
        )

        async def emit(event):
            emitted.append(event)

        event = await source._emit_once(emit)

        assert emitted == [event]
        assert event.event_type == "fr02.inspection.requested"
        assert event.target_agent_id == "orchestrator"

    asyncio.run(_run())


def _fields(table_id: str) -> list[dict[str, Any]]:
    names = {
        "tbl_task": ["任务名称", "负责人", "截止时间", "进度说明", "更新时间", "状态"],
        "tbl_milestone": ["里程碑名称", "负责人", "截止时间", "进度说明", "状态"],
        "tbl_minutes": ["标题", "纪要内容", "已同步"],
        "tbl_risk": ["风险名称", "风险描述"],
        "tbl_project": ["项目名称", "负责人"],
    }.get(table_id, [])
    return [{"field_name": name, "field_id": f"fld_{index}"} for index, name in enumerate(names)]


def _records(table_id: str) -> list[dict[str, Any]]:
    if table_id == "tbl_task":
        return [
            {
                "record_id": "rec_task_1",
                "fields": {
                    "任务名称": "供应商接口联调",
                    "负责人": "",
                    "截止时间": "2026-01-01",
                    "进度说明": "",
                    "更新时间": "2026-01-03",
                    "状态": "进行中",
                },
            }
        ]
    if table_id == "tbl_milestone":
        return [
            {
                "record_id": "rec_milestone_1",
                "fields": {"里程碑名称": "Beta 发布", "负责人": "张三", "截止时间": "", "进度说明": "", "状态": "进行中"},
            }
        ]
    if table_id == "tbl_minutes":
        return [
            {
                "record_id": "rec_min_1",
                "fields": {"标题": "供应商例会", "纪要内容": "风险：供应商延期，需要跟进。", "已同步": False},
            }
        ]
    return []


def _schema_only_fields(table_id: str) -> list[dict[str, Any]]:
    names = {
        "tbl_1": ["事项", "责任人", "到期时间", "进展说明", "最近更新", "任务状态"],
        "tbl_2": ["节点名称", "里程碑", "计划完成时间", "责任人", "状态"],
        "tbl_3": ["会议日期", "纪要内容", "行动项", "同步状态"],
    }.get(table_id, [])
    return [{"field_name": name, "field_id": f"fld_{index}"} for index, name in enumerate(names)]


def _schema_only_records(table_id: str) -> list[dict[str, Any]]:
    if table_id == "tbl_1":
        return [
            {
                "record_id": "rec_task_schema",
                "fields": {
                    "事项": "合同确认",
                    "责任人": "",
                    "到期时间": "2026-01-01",
                    "进展说明": "",
                    "最近更新": "2026-01-03",
                    "任务状态": "进行中",
                },
            }
        ]
    if table_id == "tbl_3":
        return [{"record_id": "rec_min_schema", "fields": {"纪要内容": "问题：上线窗口未确认", "同步状态": "否"}}]
    return []
