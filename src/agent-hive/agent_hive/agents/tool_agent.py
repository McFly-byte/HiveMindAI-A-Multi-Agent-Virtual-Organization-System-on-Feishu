from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_hive.agents.base import BaseAgent
from agent_hive.context.manager import AgentContext
from agent_hive.observability.logging import get_logger
from agent_hive.runtime.errors import ToolExecutionError
from agent_hive.schemas.agent import AgentOutput
from agent_hive.schemas.tool import ToolIntent, ToolResult, ToolStep
from agent_hive.tools.planner import FIELD_TYPE_HINTS, ToolPlanner


logger = get_logger("agents.tool_agent")


class ToolAgent(BaseAgent):
    """Generic delegated tool agent.

    Feishu Tool Agent is configured as ``entrypoint: tool_agent`` with
    ``tool_agent.domain: feishu``. No dedicated Feishu Python agent class is
    needed.
    """

    def __init__(self, *args: Any, planner: ToolPlanner | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.planner = planner or ToolPlanner()

    async def run(self, context: AgentContext, payload: dict[str, Any]) -> AgentOutput:
        if self.tool_executor is None:
            raise ToolExecutionError("tool_agent requires ToolExecutor")
        step = context.session.add_step("agent", "tool_agent.run", str(payload)[:500])
        intent = ToolIntent.model_validate(payload.get("intent") or payload)
        logger.info(
            "tool agent received intent run_id=%s agent_id=%s intent_id=%s domain=%s action=%s requested_by=%s",
            context.session.run_id,
            self.config.agent_id,
            intent.intent_id,
            intent.domain,
            intent.action,
            intent.requested_by_agent_id,
        )
        result = await self.execute_intent(context, intent)
        step.finish(result.summary, {"ok": result.ok, "domain": intent.domain, "action": intent.action})
        logger.info(
            "tool agent finished intent run_id=%s agent_id=%s intent_id=%s ok=%s summary=%s",
            context.session.run_id,
            self.config.agent_id,
            intent.intent_id,
            result.ok,
            result.summary,
        )
        return AgentOutput(
            agent_id=self.config.agent_id,
            run_id=context.session.run_id,
            summary=result.summary,
            payload=result.model_dump(mode="json"),
            tool_results=[result],
        )

    async def execute_intent(self, context: AgentContext, intent: ToolIntent) -> ToolResult:
        if intent.domain == "feishu.bitable" and intent.action in {"inspect_data_gaps", "fr02_inspection"}:
            return await self._execute_bitable_data_gap_inspection(context, intent)
        if intent.domain == "feishu.bitable" and intent.action in {"add_field", "create_field"}:
            return await self._execute_bitable_add_field(context, intent)
        plan = self.planner.plan(intent)
        if not plan.steps:
            logger.warning("tool intent unsupported intent_id=%s domain=%s action=%s", intent.intent_id, intent.domain, intent.action)
            return ToolResult(
                ok=False,
                intent_id=intent.intent_id,
                plan_id=plan.plan_id,
                domain=intent.domain,
                action=intent.action,
                error=f"no planner for intent {intent.domain}.{intent.action}",
                summary="unsupported tool intent",
            )
        step_results = []
        for planned_step in plan.steps:
            logger.info(
                "tool agent executing planned step intent_id=%s provider=%s tool=%s",
                intent.intent_id,
                planned_step.provider,
                planned_step.tool_name,
            )
            result = await self.tool_executor.execute_step(planned_step)  # type: ignore[union-attr]
            step_results.append(result.model_dump(mode="json"))
            if not result.ok:
                return ToolResult(
                    ok=False,
                    intent_id=intent.intent_id,
                    plan_id=plan.plan_id,
                    domain=intent.domain,
                    action=intent.action,
                    error=result.error,
                    summary=result.summary,
                    step_results=step_results,
                )
        return ToolResult(
            ok=True,
            intent_id=intent.intent_id,
            plan_id=plan.plan_id,
            domain=intent.domain,
            action=intent.action,
            summary=plan.summary,
            step_results=step_results,
        )

    async def _execute_bitable_add_field(self, context: AgentContext, intent: ToolIntent) -> ToolResult:
        app_token = _resolve_app_token(context, intent)
        table_id = await self._resolve_table_id(context, intent, app_token)
        field_name = str(intent.arguments.get("field_name") or "").strip()
        if not field_name:
            raise ToolExecutionError("feishu.bitable.add_field requires arguments.field_name")
        logger.info(
            "executing bitable add_field intent_id=%s table_id=%s field_name=%s",
            intent.intent_id,
            table_id,
            field_name,
        )

        fields = await self._call_feishu(
            "feishu_bitable_list_fields",
            {"app_token": app_token, "table_id": table_id, "page_size": 500},
        )
        existing = _find_by_name(fields.get("items") or [], field_name)
        if existing:
            logger.info("bitable field already exists intent_id=%s field_name=%s", intent.intent_id, field_name)
            return ToolResult(
                ok=True,
                intent_id=intent.intent_id,
                domain=intent.domain,
                action=intent.action,
                summary=f"field already exists: {field_name}",
                data={"field": existing, "created": False},
            )

        field_type = _field_type(intent.arguments)
        create_args = {
            "app_token": app_token,
            "table_id": table_id,
            "field_name": field_name,
            "field_type": field_type,
            "property": _field_property(intent.arguments),
        }
        created = await self._call_feishu("feishu_bitable_create_field", create_args)
        verify_after_write = bool(intent.constraints.get("verify_after_write", True))
        verified = None
        if verify_after_write:
            verified_fields = await self._call_feishu(
                "feishu_bitable_list_fields",
                {"app_token": app_token, "table_id": table_id, "page_size": 500},
            )
            verified = _find_by_name(verified_fields.get("items") or [], field_name)
        return ToolResult(
            ok=True,
            intent_id=intent.intent_id,
            domain=intent.domain,
            action=intent.action,
            summary=f"created field: {field_name}",
            data={"field": created, "verified_field": verified, "created": True},
        )

    async def _execute_bitable_data_gap_inspection(self, context: AgentContext, intent: ToolIntent) -> ToolResult:
        resource = await self._resolve_fr02_resource(context, intent)
        app_token = resource["app_token"]
        table_data: dict[str, dict[str, Any]] = {}
        for role in ("project", "task", "milestone", "meeting_minutes", "risk"):
            table = resource["tables"].get(role)
            if not table:
                continue
            table_id = str(table.get("table_id") or "")
            field_items = table.get("_fields")
            if isinstance(field_items, list):
                fields = {"items": field_items}
            else:
                fields = await self._call_feishu(
                    "feishu_bitable_list_fields",
                    {"app_token": app_token, "table_id": table_id, "page_size": 500},
                )
            records = await self._search_all_records(app_token=app_token, table_id=table_id)
            table_data[role] = {
                "table_id": table_id,
                "table_name": table.get("name") or table.get("table_name") or role,
                "fields": fields.get("items") or [],
                "records": records,
            }

        findings = _analyze_fr02_table_data(table_data, stale_days=int(intent.arguments.get("stale_days") or 7))
        result_data = {
            "resource": resource,
            "record_counts": {role: len(data.get("records") or []) for role, data in table_data.items()},
            "findings": findings,
        }
        await self._remember_fr02_result(context, intent, result_data)
        return ToolResult(
            ok=True,
            intent_id=intent.intent_id,
            domain=intent.domain,
            action=intent.action,
            summary=_fr02_summary(findings),
            data=result_data,
        )

    async def _resolve_fr02_resource(self, context: AgentContext, intent: ToolIntent) -> dict[str, Any]:
        app_token = _first_non_empty(
            intent.arguments.get("app_token"),
            intent.target.get("app_token"),
            context.event.payload.get("app_token"),
            context.event.payload.get("base_app_token"),
        )
        knowledge_space_name = _first_non_empty(
            intent.target.get("knowledge_space_name"),
            intent.arguments.get("knowledge_space_name"),
            context.event.payload.get("knowledge_space_name"),
            "项目中枢",
        )
        base_name = _first_non_empty(
            intent.target.get("base_name"),
            intent.target.get("app_name"),
            intent.arguments.get("base_name"),
            intent.arguments.get("app_name"),
            context.event.payload.get("base_name"),
            "enterprise_rag表",
        )
        if not app_token:
            app_token = await self._find_bitable_app_in_wiki(knowledge_space_name=knowledge_space_name, base_name=base_name)

        table_names = _table_names(intent)
        tables = await self._resolve_fr02_tables(app_token, table_names)
        return {
            "knowledge_space_name": knowledge_space_name,
            "base_name": base_name,
            "app_token": app_token,
            "tables": tables,
        }

    async def _find_bitable_app_in_wiki(self, *, knowledge_space_name: str, base_name: str) -> str:
        spaces = await self._list_all_pages("feishu_wiki_list_spaces", {"page_size": 50})
        space = _find_by_name(spaces, knowledge_space_name)
        if not space:
            raise ToolExecutionError(f"Feishu Wiki space not found: {knowledge_space_name}")
        space_id = str(space.get("space_id") or "")
        queue = [""]
        scanned = 0
        fallback: dict[str, Any] | None = None
        while queue and scanned < 500:
            parent = queue.pop(0)
            nodes = await self._list_all_pages(
                "feishu_wiki_list_nodes",
                {"space_id": space_id, "parent_node_token": parent, "page_size": 50},
            )
            for node in nodes:
                scanned += 1
                if node.get("has_child") and node.get("node_token"):
                    queue.append(str(node["node_token"]))
                title = str(node.get("title") or "").strip()
                if node.get("obj_type") != "bitable":
                    continue
                if title == base_name:
                    return str(node.get("obj_token") or "")
                if base_name in title or title in base_name:
                    fallback = node
        if fallback:
            return str(fallback.get("obj_token") or "")
        raise ToolExecutionError(f"Feishu Bitable not found in Wiki space {knowledge_space_name!r}: {base_name}")

    async def _resolve_fr02_tables(self, app_token: str, table_names: dict[str, str]) -> dict[str, dict[str, Any]]:
        tables = await self._call_feishu("feishu_bitable_list_tables", {"app_token": app_token, "page_size": 500})
        items = tables.get("items") or []
        logger.info("FR-02 available bitable tables app_token=%s tables=%s", app_token, [_table_brief(item) for item in items])
        resolved: dict[str, dict[str, Any]] = {}
        used_table_ids: set[str] = set()
        for role, preferred_name in table_names.items():
            aliases = _table_aliases(role, preferred_name)
            match = _find_first_by_names(items, aliases)
            if match:
                table = dict(match)
                table["_match_method"] = "name"
                resolved[role] = table
                if table.get("table_id"):
                    used_table_ids.add(str(table["table_id"]))

        unresolved = [role for role in table_names if role not in resolved]
        if unresolved:
            schema_matches = await self._resolve_fr02_tables_by_schema(app_token, items, unresolved, used_table_ids)
            for role, table in schema_matches.items():
                resolved[role] = table
                if table.get("table_id"):
                    used_table_ids.add(str(table["table_id"]))

        for role in table_names:
            if role not in resolved:
                logger.warning(
                    "FR-02 table not found role=%s aliases=%s available_tables=%s",
                    role,
                    _table_aliases(role, table_names[role]),
                    [_table_brief(item) for item in items],
                )
        return resolved

    async def _resolve_fr02_tables_by_schema(
        self,
        app_token: str,
        tables: list[Any],
        unresolved_roles: list[str],
        used_table_ids: set[str],
    ) -> dict[str, dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for table in tables:
            if not isinstance(table, dict):
                continue
            table_id = str(table.get("table_id") or "")
            if not table_id:
                continue
            fields = await self._call_feishu(
                "feishu_bitable_list_fields",
                {"app_token": app_token, "table_id": table_id, "page_size": 500},
            )
            field_items = fields.get("items") or []
            field_names = _field_names(field_items)
            candidate = {"table": table, "fields": field_items, "field_names": field_names}
            logger.debug(
                "FR-02 table schema candidate table=%s fields=%s",
                _table_brief(table),
                field_names,
            )
            candidates.append(candidate)

        scored: list[tuple[float, str, dict[str, Any], list[str]]] = []
        for role in unresolved_roles:
            for candidate in candidates:
                table = candidate["table"]
                table_id = str(table.get("table_id") or "")
                if table_id in used_table_ids:
                    continue
                score, reasons = _score_table_role(
                    role,
                    table_name=str(table.get("name") or table.get("table_name") or ""),
                    field_names=candidate["field_names"],
                )
                if score >= _minimum_schema_score(role):
                    scored.append((score, role, candidate, reasons))

        resolved: dict[str, dict[str, Any]] = {}
        assigned_roles: set[str] = set()
        assigned_table_ids: set[str] = set()
        for score, role, candidate, reasons in sorted(scored, key=lambda item: item[0], reverse=True):
            table = candidate["table"]
            table_id = str(table.get("table_id") or "")
            if role in assigned_roles or table_id in assigned_table_ids:
                continue
            enriched = dict(table)
            enriched["_match_method"] = "schema"
            enriched["_schema_score"] = score
            enriched["_schema_reasons"] = reasons
            enriched["_fields"] = candidate["fields"]
            resolved[role] = enriched
            assigned_roles.add(role)
            assigned_table_ids.add(table_id)
            logger.info(
                "FR-02 table resolved by schema role=%s table=%s score=%.1f reasons=%s",
                role,
                _table_brief(table),
                score,
                reasons,
            )
        return resolved

    async def _list_all_pages(self, tool_name: str, arguments: dict[str, Any], *, max_pages: int = 20) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = str(arguments.get("page_token") or "")
        for _ in range(max_pages):
            args = dict(arguments)
            if page_token:
                args["page_token"] = page_token
            data = await self._call_feishu(tool_name, args)
            items.extend(item for item in data.get("items") or [] if isinstance(item, dict))
            if not data.get("has_more"):
                break
            page_token = str(data.get("page_token") or "")
            if not page_token:
                break
        return items

    async def _search_all_records(self, *, app_token: str, table_id: str, max_pages: int = 20) -> list[dict[str, Any]]:
        return await self._list_all_pages(
            "feishu_bitable_search_records",
            {"app_token": app_token, "table_id": table_id, "page_size": 500, "automatic_fields": True},
            max_pages=max_pages,
        )

    async def _remember_fr02_result(self, context: AgentContext, intent: ToolIntent, result_data: dict[str, Any]) -> None:
        if self.memory_manager is None or intent.constraints.get("remember_discovered_resources", True) is False:
            return
        resource = result_data["resource"]
        await self.memory_manager.write(
            content=json.dumps({"kind": "feishu_resource_discovery", "resource": resource}, ensure_ascii=False, default=str),
            agent_id=self.config.agent_id,
            project_id=context.session.project_id,
            run_id=context.session.run_id,
            tags=["feishu_resource", "fr02", "bitable"],
            metadata={
                "kind": "feishu_resource_discovery",
                "base_name": resource.get("base_name"),
                "knowledge_space_name": resource.get("knowledge_space_name"),
            },
        )
        await self.memory_manager.write(
            content=json.dumps({"kind": "fr02_inspection_result", **result_data}, ensure_ascii=False, default=str),
            agent_id=self.config.agent_id,
            project_id=context.session.project_id,
            run_id=context.session.run_id,
            tags=["fr02_inspection", "data_gap"],
            metadata={"kind": "fr02_inspection_result"},
        )

    async def _resolve_table_id(self, context: AgentContext, intent: ToolIntent, app_token: str) -> str:
        table_id = str(intent.target.get("table_id") or intent.arguments.get("table_id") or "").strip()
        if table_id:
            return table_id
        table_name = str(intent.target.get("table_name") or intent.arguments.get("table_name") or "").strip()
        if not table_name:
            raise ToolExecutionError("Feishu Bitable intent requires target.table_id or target.table_name")
        tables = await self._call_feishu("feishu_bitable_list_tables", {"app_token": app_token, "page_size": 500})
        match = _find_by_name(tables.get("items") or [], table_name)
        if not match:
            raise ToolExecutionError(f"Feishu Bitable table not found: {table_name}")
        return str(match.get("table_id") or "")

    async def _call_feishu(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.tool_executor is None:
            raise ToolExecutionError("tool_agent requires ToolExecutor")
        logger.info("tool agent calling Feishu adapter tool=%s argument_keys=%s", tool_name, sorted(arguments))
        result = await self.tool_executor.execute_step(
            ToolStep(provider="feishu", tool_name=tool_name, arguments=arguments, purpose=tool_name)
        )
        if not result.ok:
            raise ToolExecutionError(result.error or f"{tool_name} failed")
        return result.data


def _resolve_app_token(context: AgentContext, intent: ToolIntent) -> str:
    candidates = [
        intent.arguments.get("app_token"),
        intent.target.get("app_token"),
        context.event.payload.get("app_token"),
        context.event.payload.get("base_app_token"),
    ]
    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip()
    raise ToolExecutionError("Feishu intent requires app_token in arguments, target, or event payload")


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _table_names(intent: ToolIntent) -> dict[str, str]:
    defaults = {
        "project": "项目表",
        "task": "任务表",
        "milestone": "里程碑表",
        "meeting_minutes": "会议纪要表",
        "risk": "风险表",
    }
    raw = intent.arguments.get("table_names") or intent.target.get("table_names")
    if not isinstance(raw, dict):
        return defaults
    merged = dict(defaults)
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            merged[key] = value.strip()
    return merged


def _table_aliases(role: str, preferred_name: str) -> list[str]:
    aliases = {
        "project": ["项目表", "项目", "Projects", "Project"],
        "task": ["任务表", "任务", "Tasks", "Task"],
        "milestone": ["里程碑表", "里程碑", "Milestones", "Milestone"],
        "meeting_minutes": ["会议纪要表", "会议纪要", "纪要表", "会议记录", "Minutes"],
        "risk": ["风险表", "风险", "Risks", "Risk"],
    }.get(role, [])
    return [preferred_name, *[item for item in aliases if item != preferred_name]]


def _table_brief(item: Any) -> dict[str, str]:
    if not isinstance(item, dict):
        return {"table_id": "", "name": str(item)}
    return {
        "table_id": str(item.get("table_id") or ""),
        "name": str(item.get("name") or item.get("table_name") or ""),
    }


def _score_table_role(role: str, *, table_name: str, field_names: list[str]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    aliases = _table_aliases(role, "")
    table_name_l = table_name.lower()
    for alias in aliases:
        alias = alias.strip()
        if alias and (alias == table_name or alias.lower() in table_name_l or table_name_l in alias.lower()):
            score += 4
            reasons.append(f"name:{alias}")
            break

    for group_aliases, weight in _role_field_hints(role):
        matched = _match_field(field_names, group_aliases)
        if matched:
            score += weight
            reasons.append(f"field:{matched}")
    return score, reasons


def _minimum_schema_score(role: str) -> float:
    return {
        "project": 3.0,
        "task": 5.0,
        "milestone": 5.0,
        "meeting_minutes": 4.0,
        "risk": 4.0,
    }.get(role, 4.0)


def _role_field_hints(role: str) -> list[tuple[list[str], float]]:
    hints = {
        "project": [
            (["项目名称", "项目", "项目编号", "项目代号", "project", "project name"], 3),
            (["负责人", "项目负责人", "PM", "owner"], 1.5),
            (["状态", "项目状态", "status"], 1),
        ],
        "task": [
            (["任务名称", "任务", "事项", "待办", "task", "todo"], 3),
            (["负责人", "责任人", "owner", "assignee"], 1.5),
            (["截止时间", "截止日期", "到期时间", "deadline", "due"], 1.5),
            (["进度说明", "进展说明", "进度", "progress"], 1.5),
            (["状态", "任务状态", "status"], 1),
        ],
        "milestone": [
            (["里程碑名称", "里程碑", "milestone"], 3),
            (["计划完成时间", "截止时间", "截止日期", "deadline", "due"], 1.5),
            (["负责人", "责任人", "owner"], 1),
            (["状态", "里程碑状态", "status"], 1),
            (["进度说明", "进度", "progress"], 1),
        ],
        "meeting_minutes": [
            (["会议纪要", "纪要内容", "会议记录", "minutes", "meeting"], 3),
            (["行动项", "待办", "问题", "风险", "action", "issue", "risk"], 1.5),
            (["已同步", "同步状态", "是否同步", "sync", "synced"], 1.5),
            (["会议时间", "会议日期", "meeting date", "date"], 1),
        ],
        "risk": [
            (["风险名称", "风险", "risk"], 3),
            (["风险描述", "描述", "description"], 1.5),
            (["风险等级", "等级", "级别", "level", "priority"], 1.5),
            (["应对措施", "缓解措施", "mitigation", "response"], 1),
            (["负责人", "责任人", "owner"], 1),
        ],
    }
    return hints.get(role, [])


def _find_first_by_names(items: list[Any], names: list[str]) -> dict[str, Any] | None:
    for name in names:
        match = _find_by_name(items, name)
        if match:
            return match
    lowered = [name.lower() for name in names]
    for item in items:
        if not isinstance(item, dict):
            continue
        value = str(item.get("name") or item.get("table_name") or "").strip().lower()
        if any(name in value or value in name for name in lowered if name):
            return item
    return None


def _find_by_name(items: list[Any], name: str) -> dict[str, Any] | None:
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get("name") or item.get("table_name") or item.get("field_name") or item.get("view_name")
        if str(value or "").strip() == name:
            return item
    return None


def _field_type(args: dict[str, Any]) -> int:
    raw = args.get("field_type")
    if isinstance(raw, int):
        return raw
    hint = str(args.get("field_type_hint") or "text").strip()
    return FIELD_TYPE_HINTS.get(hint, 1)


def _field_property(args: dict[str, Any]) -> dict[str, Any]:
    raw_options = args.get("options")
    if isinstance(raw_options, list) and raw_options:
        return {"options": [{"name": str(item)} for item in raw_options]}
    prop = args.get("property")
    return prop if isinstance(prop, dict) else {}


def _analyze_fr02_table_data(table_data: dict[str, dict[str, Any]], *, stale_days: int) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    findings: dict[str, Any] = {
        "missing_fields": [],
        "overdue_unupdated_tasks": [],
        "unsynced_meeting_minutes": [],
        "warnings": [],
    }
    for role in ("task", "milestone"):
        data = table_data.get(role)
        if not data:
            findings["warnings"].append({"code": "table_missing", "table_role": role, "message": f"{role} table not found"})
            continue
        _inspect_required_fields(data, table_role=role, findings=findings)
        _inspect_overdue_records(data, table_role=role, findings=findings, today=today, stale_days=stale_days)

    if "meeting_minutes" in table_data:
        _inspect_meeting_minutes_sync(table_data, findings=findings)
    else:
        findings["warnings"].append(
            {"code": "table_missing", "table_role": "meeting_minutes", "message": "meeting minutes table not found"}
        )
    return findings


def _inspect_required_fields(data: dict[str, Any], *, table_role: str, findings: dict[str, Any]) -> None:
    field_names = _field_names(data.get("fields") or [])
    mapping = _field_mapping(field_names)
    for record in data.get("records") or []:
        fields = _record_fields(record)
        missing = []
        if _is_empty(_get_field(fields, mapping["owner"])):
            missing.append("负责人")
        if _is_empty(_get_field(fields, mapping["deadline"])):
            missing.append("截止时间")
        if _is_empty(_get_field(fields, mapping["progress"])):
            missing.append("进度说明")
        if missing:
            findings["missing_fields"].append(
                {
                    "table_role": table_role,
                    "table_name": data.get("table_name"),
                    "record_id": record.get("record_id"),
                    "title": _record_title(fields, mapping),
                    "missing": missing,
                }
            )


def _inspect_overdue_records(
    data: dict[str, Any],
    *,
    table_role: str,
    findings: dict[str, Any],
    today: Any,
    stale_days: int,
) -> None:
    field_names = _field_names(data.get("fields") or [])
    mapping = _field_mapping(field_names)
    for record in data.get("records") or []:
        fields = _record_fields(record)
        deadline = _parse_date(_get_field(fields, mapping["deadline"]))
        if deadline is None or deadline >= today:
            continue
        status = _stringify(_get_field(fields, mapping["status"]))
        if _is_done(status):
            continue
        updated_at = _parse_date(_get_field(fields, mapping["updated_at"]))
        progress = _get_field(fields, mapping["progress"])
        stale = updated_at is None or updated_at <= today - timedelta(days=stale_days)
        if stale or _is_empty(progress):
            findings["overdue_unupdated_tasks"].append(
                {
                    "table_role": table_role,
                    "table_name": data.get("table_name"),
                    "record_id": record.get("record_id"),
                    "title": _record_title(fields, mapping),
                    "deadline": str(deadline),
                    "updated_at": str(updated_at) if updated_at else "",
                    "status": status,
                    "stale_days": stale_days,
                    "reason": "overdue and stale or missing progress",
                }
            )


def _inspect_meeting_minutes_sync(table_data: dict[str, dict[str, Any]], *, findings: dict[str, Any]) -> None:
    minutes = table_data.get("meeting_minutes") or {}
    risk_task_text = _known_task_and_risk_text(table_data)
    field_names = _field_names(minutes.get("fields") or [])
    mapping = _field_mapping(field_names)
    sync_field = _match_field(field_names, ["已同步", "同步状态", "是否同步", "已同步任务", "已同步风险", "sync", "synced"])
    content_field = _match_field(field_names, ["纪要内容", "内容", "问题", "风险", "待办", "行动项", "摘要", "结论", "标题", "名称"])
    for record in minutes.get("records") or []:
        fields = _record_fields(record)
        title = _record_title(fields, mapping)
        content = _stringify(_get_field(fields, content_field)) or _stringify(fields)
        if not _looks_like_action_or_issue(content):
            continue
        if sync_field and _truthy(_get_field(fields, sync_field)):
            continue
        probe = (title + " " + content).strip()
        if _has_loose_match(probe, risk_task_text):
            continue
        findings["unsynced_meeting_minutes"].append(
            {
                "table_role": "meeting_minutes",
                "table_name": minutes.get("table_name"),
                "record_id": record.get("record_id"),
                "title": title,
                "reason": "meeting minute mentions issue/action but no sync flag or matching task/risk record was found",
            }
        )


def _field_names(fields: list[Any]) -> list[str]:
    names: list[str] = []
    for field in fields:
        if isinstance(field, dict):
            name = field.get("field_name") or field.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return names


def _field_mapping(field_names: list[str]) -> dict[str, str]:
    return {
        "title": _match_field(field_names, ["任务名称", "里程碑名称", "项目名称", "标题", "名称", "title", "name"]),
        "owner": _match_field(field_names, ["负责人", "责任人", "Owner", "owner", "负责", "PM"]),
        "deadline": _match_field(field_names, ["截止时间", "截止日期", "计划完成时间", "到期时间", "Deadline", "deadline", "Due Date", "due_date"]),
        "progress": _match_field(field_names, ["进度说明", "进展说明", "进度", "Progress", "progress", "说明", "更新说明"]),
        "updated_at": _match_field(field_names, ["更新时间", "最后更新时间", "最近更新", "最后修改时间", "更新日期", "update_time", "last_updated"]),
        "status": _match_field(field_names, ["状态", "任务状态", "里程碑状态", "Status", "status"]),
    }


def _match_field(field_names: list[str], aliases: list[str]) -> str:
    for alias in aliases:
        for name in field_names:
            if name == alias:
                return name
    lowered = [(name, name.lower()) for name in field_names]
    for alias in aliases:
        alias_l = alias.lower()
        for original, name_l in lowered:
            if alias_l in name_l or name_l in alias_l:
                return original
    return ""


def _record_fields(record: Any) -> dict[str, Any]:
    if isinstance(record, dict) and isinstance(record.get("fields"), dict):
        return record["fields"]
    return {}


def _get_field(fields: dict[str, Any], name: str) -> Any:
    if name and name in fields:
        return fields[name]
    return None


def _record_title(fields: dict[str, Any], mapping: dict[str, str]) -> str:
    title = _stringify(_get_field(fields, mapping["title"]))
    if title:
        return title
    for key in ("任务名称", "里程碑名称", "项目名称", "标题", "名称"):
        if key in fields:
            return _stringify(fields[key])
    return ""


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _stringify(value).strip().lower()
    return text in {"true", "yes", "y", "1", "是", "已同步", "已完成", "完成", "synced", "done"}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return " ".join(_stringify(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "name", "title", "value", "email"):
            if key in value:
                return _stringify(value[key])
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _parse_date(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
    if isinstance(value, dict):
        for key in ("timestamp", "date", "value", "text"):
            parsed = _parse_date(value.get(key))
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, list) and value:
        return _parse_date(value[0])
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_date(int(text))
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    if len(text) >= 10:
        for value, fmt in ((text[:10], "%Y-%m-%d"), (text[:10], "%Y/%m/%d"), (text[:10], "%Y.%m.%d")):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    try:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        return None


def _is_done(status: str) -> bool:
    value = status.strip().lower()
    return value in {"完成", "已完成", "关闭", "已关闭", "done", "closed", "finished", "resolved", "取消", "cancelled"}


def _looks_like_action_or_issue(text: str) -> bool:
    if not text:
        return False
    keywords = ("问题", "风险", "阻塞", "待办", "行动项", "跟进", "TODO", "todo", "action", "risk", "issue", "blocker")
    return any(keyword in text for keyword in keywords)


def _known_task_and_risk_text(table_data: dict[str, dict[str, Any]]) -> str:
    chunks: list[str] = []
    for role in ("task", "risk"):
        for record in (table_data.get(role) or {}).get("records") or []:
            chunks.append(_stringify(_record_fields(record)))
    return "\n".join(chunks)


def _has_loose_match(probe: str, corpus: str) -> bool:
    if not probe or not corpus:
        return False
    cleaned = "".join(ch for ch in probe if not ch.isspace())
    if len(cleaned) < 8:
        return cleaned in corpus
    return cleaned[:24] in "".join(ch for ch in corpus if not ch.isspace())


def _fr02_summary(findings: dict[str, Any]) -> str:
    missing = len(findings.get("missing_fields") or [])
    overdue = len(findings.get("overdue_unupdated_tasks") or [])
    unsynced = len(findings.get("unsynced_meeting_minutes") or [])
    warnings = len(findings.get("warnings") or [])
    return f"FR-02 inspection completed: missing={missing}, overdue_unupdated={overdue}, unsynced_minutes={unsynced}, warnings={warnings}"
