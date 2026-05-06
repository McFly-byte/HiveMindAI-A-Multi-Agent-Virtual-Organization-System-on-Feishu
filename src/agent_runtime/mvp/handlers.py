from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from agent_runtime.agent_io import (
    AbnormalSignal,
    CoordinatorAction,
    CoordinatorPlan,
    FollowUpInput,
    FollowUpOutput,
    FollowUpRequest,
    MilestoneSnapshot,
    MissingFieldFinding,
    ProjectRecordSnapshot,
    ProjectStateInput,
    ProjectStateOutput,
    RiskAnalysisInput,
    RiskAnalysisOutput,
    RiskCandidate,
    TaskSnapshot,
    WeeklyReportDraft,
    WeeklyReportInput,
    WeeklyReportOutput,
    WeeklyReportSection,
)
from agent_runtime.base_refs import EvidenceRef, OutputRecordRef, RecordCreate, RecordPatch
from agent_runtime.enums import (
    ActionType,
    AgentName,
    BaseTableName,
    EvidenceSourceType,
    ErrorType,
    EventType,
    FollowUpStatus,
    MilestoneStatus,
    Priority,
    ProjectHealth,
    ProjectStatus,
    ReportSendStatus,
    RiskStatus,
    RiskLevel,
    RiskType,
    TaskStatus,
)
from agent_runtime.errors import AgentRuntimeError
from agent_runtime.context import AgentContext
from agent_runtime.events import AgentCallRequest, AgentTriggerEvent
from agent_runtime.interfaces import QualityGateProtocol, ToolExecutorProtocol
from agent_runtime.loaders import load_agent_config, load_agent_prompt, load_project_manifest
from agent_runtime.mvp.bitable_fields import (
    record_field_bool,
    record_field_date,
    record_field_number,
    record_field_text,
    record_field_texts,
)
from agent_runtime.mvp.project_env import expand_env_value
from agent_runtime.project_state import ProjectManifest
from agent_runtime.quality_gate import QualityGateRequest
from agent_runtime.session import AgentSession, LLMCallRecord, RuntimeErrorInfo


def _coerce_dict(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str) and payload.strip():
        return json.loads(payload)
    raise AgentRuntimeError(f"Unsupported payload type for dict coercion: {type(payload)!r}")


def _unwrap_tool_result(out: dict[str, Any]) -> dict[str, Any]:
    if not out.get("ok", False):
        raise AgentRuntimeError(out.get("message") or "tool call failed")
    result = out.get("result")
    if isinstance(result, dict):
        return result
    return {}


def _now_ms() -> int:
    return int(datetime.utcnow().timestamp() * 1000)


def _iso_week(value: date | None = None) -> str:
    today = value or date.today()
    year, week, _ = today.isocalendar()
    return f"{year}-W{week:02d}"


def _compact_lines(values: list[str], *, limit: int = 1600) -> str:
    text = "\n".join(v for v in values if v)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _compact_json(value: Any, *, limit: int = 1600) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _env_bool(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "y", "on"}


def _evidence(
    *,
    table_name: BaseTableName,
    record_id: str,
    summary: str,
    field_name: str | None = None,
    value_snapshot: Any | None = None,
    source_type: EvidenceSourceType = EvidenceSourceType.BASE_RECORD,
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"ev_{uuid4().hex[:10]}",
        source_type=source_type,
        summary=summary,
        table_name=table_name,
        record_id=record_id,
        field_name=field_name,
        value_snapshot=value_snapshot,
    )


def _evidence_summary(evidence_refs: list[EvidenceRef]) -> str:
    rows: list[str] = []
    for ev in evidence_refs:
        location = ""
        if ev.table_name and ev.record_id:
            location = f"{ev.table_name}:{ev.record_id}"
        if ev.field_name:
            location = f"{location}.{ev.field_name}" if location else ev.field_name
        rows.append(f"{location} - {ev.summary}" if location else ev.summary)
    return _compact_lines(rows, limit=1800)


def _existing_idempotency_keys(items: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for row in items:
        fields = row.get("fields") if isinstance(row, dict) else None
        if not isinstance(fields, dict):
            continue
        key = record_field_text(fields, "幂等键")
        if key:
            keys.add(key)
    return keys


_FIELD_ALIASES: dict[BaseTableName, dict[str, tuple[str, ...]]] = {
    BaseTableName.RISKS: {
        "风险标题": ("ID", "标题"),
        "由哪个 Agent 创建": ("由哪个 Agent创建", "创建 Agent", "创建Agent"),
    },
    BaseTableName.FOLLOWUPS: {
        "追问标题": ("ID", "标题"),
    },
    BaseTableName.WEEKLY_REPORTS: {
        "由哪个 Agent 创建": ("由哪个 Agent创建", "创建 Agent", "创建Agent"),
    },
}


def _field_name(item: dict[str, Any]) -> str:
    value = item.get("field_name") or item.get("name")
    return str(value) if value else ""


def _field_type(item: dict[str, Any]) -> int:
    value = item.get("type", item.get("field_type", 0))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _record_ids_from_value(value: Any) -> list[str]:
    values: list[Any]
    if value is None or value == "":
        return []
    if isinstance(value, list):
        values = value
    else:
        values = [part.strip() for part in str(value).replace("\n", ",").split(",")]

    record_ids: list[str] = []
    for item in values:
        if isinstance(item, dict):
            raw = item.get("record_id") or item.get("id") or item.get("text") or item.get("value")
        else:
            raw = item
        text = str(raw or "").strip()
        if text.startswith("rec") and text not in record_ids:
            record_ids.append(text)
    return record_ids


def _first_project_member(manifest: ProjectManifest, role_type: str) -> str | None:
    for member in manifest.members:
        if member.role_type == role_type:
            return member.name
    return manifest.members[0].name if manifest.members else None


def _task_done(status: TaskStatus) -> bool:
    return status == TaskStatus.DONE


def _milestone_done(status: MilestoneStatus) -> bool:
    return status == MilestoneStatus.DONE


def _risk_level_from_signal(severity: str) -> RiskLevel:
    if severity == "high":
        return RiskLevel.HIGH
    if severity == "medium":
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _risk_type_from_signal(signal_type: str) -> RiskType:
    if signal_type in {"task_overdue", "milestone_delayed", "project_release_overdue", "project_delayed"}:
        return RiskType.DELAY
    if signal_type in {"task_blocked", "milestone_dependency_missing"}:
        return RiskType.DEPENDENCY_BLOCK
    if signal_type in {"task_effort_overrun", "owner_overload"}:
        return RiskType.RESOURCE_CONFLICT
    if signal_type in {"task_stale", "status_unclear"}:
        return RiskType.COMMUNICATION_DISTORTION
    if signal_type.startswith("missing_") or signal_type == "blocked_reason_missing":
        return RiskType.DATA_MISSING
    return RiskType.DELAY


def _suggest_actions(risk_type: RiskType, level: RiskLevel) -> list[str]:
    actions = {
        RiskType.DELAY: ["确认剩余工作量与可压缩范围", "同步里程碑影响并给出新的完成日期"],
        RiskType.DEPENDENCY_BLOCK: ["明确阻塞依赖负责人", "拆分可并行任务并升级跨团队协调"],
        RiskType.RESOURCE_CONFLICT: ["核对负责人负载", "将 P0/P1 任务重新排序并确认支援人选"],
        RiskType.REQUIREMENT_CHANGE: ["冻结变更范围", "补充变更影响评估后再调整排期"],
        RiskType.COMMUNICATION_DISTORTION: ["要求责任人当天补充状态", "统一任务状态口径并记录更新时间"],
        RiskType.DATA_MISSING: ["补齐关键字段后重新评估", "由项目经理确认责任人和截止时间"],
        RiskType.QUALITY: ["补充验收标准", "安排冒烟测试与回归验证"],
    }[risk_type]
    if level == RiskLevel.HIGH:
        return [*actions, "在本周周报中列为待拍板事项"]
    return actions


def _risk_create(project_id: str, candidate: RiskCandidate) -> RecordCreate:
    fields = {
        "风险标题": candidate.risk_title,
        "所属项目": project_id,
        "关联任务": ", ".join(candidate.related_task_ids),
        "风险类型": candidate.risk_type.value,
        "风险等级": candidate.risk_level.value,
        "触发原因": candidate.trigger_reason,
        "当前状态": RiskStatus.TO_CONFIRM.value,
        "责任人": candidate.responsible_owner or "",
        "建议动作": _compact_lines(candidate.suggested_actions),
        "是否升级": candidate.risk_level == RiskLevel.HIGH,
        "升级对象": candidate.followup_target_role or ("项目负责人" if candidate.risk_level == RiskLevel.HIGH else ""),
        "最近更新时间": _now_ms(),
        "由哪个 Agent 创建": AgentName.RISK_ANALYSIS.value,
        "证据来源": _evidence_summary(candidate.evidence_refs),
        "幂等键": candidate.idempotency_key,
    }
    return RecordCreate(
        table_name=BaseTableName.RISKS,
        fields=fields,
        idempotency_key=candidate.idempotency_key,
        reason=candidate.trigger_reason,
        evidence_refs=list(candidate.evidence_refs),
    )


def _followup_create(request: FollowUpRequest) -> RecordCreate:
    fields = {
        "追问标题": request.followup_title,
        "所属项目": request.project_id,
        "关联任务": request.related_task_id or "",
        "关联风险": request.related_risk_id or "",
        "追问对象": request.target_user or "",
        "追问角色": request.target_role or "",
        "追问原因": request.followup_reason,
        "追问内容": request.message,
        "追问状态": request.status.value,
        "追问时间": _now_ms(),
        "是否已回写任务或风险表": False,
        "幂等键": request.idempotency_key,
    }
    return RecordCreate(
        table_name=BaseTableName.FOLLOWUPS,
        fields=fields,
        idempotency_key=request.idempotency_key,
        reason=request.followup_reason,
        evidence_refs=list(request.evidence_refs),
    )


def _weekly_report_create(report: WeeklyReportDraft, evidence_refs: list[EvidenceRef]) -> RecordCreate:
    idempotency_key = f"{report.project_id}:WeeklyReports:weekly_report:{report.period}"
    fields = {
        "周期": report.period,
        "所属项目": report.project_id,
        "项目摘要": report.project_summary,
        "本周进展": _compact_lines(report.progress.items),
        "风险摘要": _compact_lines(report.risk_summary.items),
        "阻塞项": _compact_lines(report.blockers.items),
        "下周计划": _compact_lines(report.next_plan.items),
        "决策建议": _compact_lines(report.decision_items.items),
        "待拍板事项": _compact_lines(report.decision_items.items),
        "发送状态": report.send_status.value,
        "生成时间": _now_ms(),
        "由哪个 Agent 创建": AgentName.WEEKLY_REPORT.value,
        "幂等键": idempotency_key,
    }
    return RecordCreate(
        table_name=BaseTableName.WEEKLY_REPORTS,
        fields=fields,
        idempotency_key=idempotency_key,
        reason=f"生成 {report.period} 项目周报",
        evidence_refs=evidence_refs,
    )


def _session_output(session: AgentSession, agent_name: AgentName) -> dict[str, Any] | None:
    key = f"{agent_name}_output"
    for item in reversed(session.memory):
        if item.key == key and isinstance(item.value, dict):
            return item.value
    return None


def _agent_run_create(session: AgentSession, description: str, *, status_override: str | None = None) -> RecordCreate:
    idempotency_key = f"agent_run:{session.run_id}"
    error_summary = "; ".join(error.message for error in session.errors)
    evidence_summary = session.final_summary or _compact_lines(
        [step.output_summary or "" for step in session.steps if step.output_summary],
        limit=1800,
    )
    output_result = evidence_summary[:1500]
    if error_summary:
        output_result = _compact_lines([output_result, f"errors: {error_summary}"], limit=1700)
    output_result = _compact_lines([output_result, f"trace_id: {session.trace_id}"], limit=1800)
    fields: dict[str, Any] = {
        "ID": session.run_id,
        "Agent名称": str(session.agent_name),
        "触发时间": int(session.started_at.timestamp() * 1000),
        "操作描述": description,
        "输入来源": ",".join(session.input_record_ids) or f"{session.trigger_type}:agent_runtime",
        "输出结果": output_result,
        "执行状态": status_override or session.status.value,
    }
    return RecordCreate(
        table_name=BaseTableName.AGENT_RUNS,
        fields=fields,
        idempotency_key=idempotency_key,
        reason=f"AgentRun log for {session.agent_name}",
    )


class _ToolCaller(Protocol):
    async def call_tool(self, tool_name: str, payload: dict[str, Any], session: AgentSession) -> dict[str, Any]:
        ...


class BaseMvpHandler:
    def __init__(self, tool_executor: ToolExecutorProtocol, project_root: Any) -> None:
        self._tools = tool_executor
        self._root = project_root

    def _manifest(self) -> ProjectManifest:
        return load_project_manifest(self._root / "projects" / "enterprise_rag")

    def _expanded_manifest(self) -> ProjectManifest:
        raw = self._manifest().model_dump(mode="json")
        data = expand_env_value(raw)
        return ProjectManifest.model_validate(data)

    async def _trace(self, session: AgentSession, message: str, **payload: Any) -> None:
        args: dict[str, Any] = {"message": message}
        if payload:
            args["payload"] = payload
        await self._tools.call_tool("trace_tool", args, session)

    def _agent_config(self, session: AgentSession):
        return load_agent_config(self._root / "agents" / str(session.agent_name) / "agent.yaml")

    def _agent_prompt(self, session: AgentSession) -> str:
        try:
            return load_agent_prompt(self._agent_config(session))
        except Exception:
            path = self._root / "agents" / str(session.agent_name) / "AGENT.md"
            return path.read_text(encoding="utf-8") if path.is_file() else str(session.agent_name)

    async def think(self, context: AgentContext, observation: dict[str, Any]) -> dict[str, Any]:
        """Default business-agent THINK phase backed by the configured LLM tool."""

        compact_context = context.render(max_chars=2600)
        loop_state = context.loop_state
        out = await self._llm_json(
            context.session,
            purpose=f"{context.session.agent_name}_think",
            instruction=(
                "你处在当前业务 Agent 的 THINK 阶段。请基于 observation、context 和 memory，"
                "判断本轮 ACT 应重点处理什么。"
                "必须输出 JSON："
                "{\"decision\":\"invoke_handler\", \"reason\":\"一句中文判断\", "
                "\"focus_items\":[\"重点1\"], \"expected_output\":\"本轮应产出的结构化结果\", "
                "\"confidence\":0.0到1.0}。"
                "不得要求直接写 Base；子 Agent 只能产出 proposed_creates/proposed_patches。"
            ),
            payload={
                "agent_name": str(context.session.agent_name),
                "project_id": context.session.project_id,
                "observation": observation,
                "context": compact_context,
                "loop_iteration": loop_state.iteration if loop_state is not None else 0,
            },
            max_tokens=800,
            temperature=0.0,
            required=False,
        )
        if not out:
            out = {
                "decision": "invoke_handler",
                "reason": "LLM 不可用，使用确定性 handler 继续执行",
                "focus_items": [],
                "expected_output": "按 Agent schema 输出结构化结果",
                "confidence": 0.0,
            }
        else:
            raw_decision = str(out.get("decision") or "").strip()
            if raw_decision and raw_decision != "invoke_handler":
                out["raw_decision"] = raw_decision
            out["decision"] = "invoke_handler"
        context.add_scratchpad("llm_thought", out)
        return out

    async def act(self, context: AgentContext, thought: dict[str, Any], input_payload: Any) -> Any:
        context.add_scratchpad(
            "act_from_thought",
            {
                "decision": thought.get("decision") if isinstance(thought, dict) else None,
                "reason": thought.get("reason") if isinstance(thought, dict) else None,
            },
        )
        return await self.run(context.session, input_payload)

    async def _llm_json(
        self,
        session: AgentSession,
        *,
        purpose: str,
        instruction: str,
        payload: dict[str, Any],
        max_tokens: int = 1200,
        temperature: float | None = None,
        required: bool = False,
    ) -> dict[str, Any]:
        if _env_bool("HIVEMIND_LLM_DISABLED"):
            if required:
                raise AgentRuntimeError("LLM is required but HIVEMIND_LLM_DISABLED is set")
            return {}

        config = self._agent_config(session)
        started = datetime.utcnow()
        prompt = (
            f"{self._agent_prompt(session)}\n\n"
            "你必须只输出一个 JSON object，不要输出 Markdown。"
            "只能基于 payload 中的 Base 事实、evidence 和 memory，不得编造事实。\n\n"
            f"当前任务：{instruction}"
        )
        provider = str(config.model.provider)
        result_json: dict[str, Any] = {}
        error: RuntimeErrorInfo | None = None
        output_summary = ""

        try:
            out = await self._tools.call_tool(
                "llm_chat_json",
                {
                    "provider": provider,
                    "model": config.model.model_name,
                    "messages": [
                        {
                            "role": "developer" if provider in {"aihubmix", "openai"} else "system",
                            "content": prompt,
                        },
                        {
                            "role": "user",
                            "content": json.dumps(payload, ensure_ascii=False, default=str),
                        },
                    ],
                    "temperature": config.model.temperature if temperature is None else temperature,
                    "max_tokens": min(config.model.max_tokens, max_tokens),
                    "timeout_seconds": config.model.timeout_seconds,
                    "json_mode": config.model.json_mode,
                    "top_p": 1.0,
                },
                session,
            )
            if not out.get("ok", False):
                raise AgentRuntimeError(str(out.get("message") or "llm_chat_json failed"))
            result = out.get("result") if isinstance(out.get("result"), dict) else {}
            parsed = result.get("json") if isinstance(result, dict) else {}
            if not isinstance(parsed, dict):
                raise AgentRuntimeError("llm_chat_json returned non-object JSON")
            result_json = parsed
            output_summary = _compact_json(result_json, limit=500)
            await self._trace(session, "llm_decision", purpose=purpose, output=result_json)
        except Exception as exc:  # noqa: BLE001 - fallback is audited below.
            error = RuntimeErrorInfo(
                error_type=ErrorType.LLM_FAILED,
                message=str(exc),
                detail=exc.__class__.__name__,
                retryable=False,
            )
            output_summary = f"LLM failed: {exc}"
            await self._trace(session, "llm_fallback", purpose=purpose, error=str(exc), required=required)
            if required:
                raise
        finally:
            if session.steps:
                session.steps[-1].llm_calls.append(
                    LLMCallRecord(
                        llm_call_id=f"llm_{uuid4().hex[:12]}",
                        provider=provider,
                        model_name=config.model.model_name,
                        prompt_summary=purpose,
                        output_summary=output_summary,
                        output_json_valid=bool(result_json),
                        error=error,
                        started_at=started,
                        ended_at=datetime.utcnow(),
                    )
                )

        return result_json

    def _llm_allowed(self, session: AgentSession) -> bool:
        if _env_bool("HIVEMIND_LLM_DISABLED"):
            return False
        try:
            config = self._agent_config(session)
        except Exception:
            return False
        if "llm_chat_json" in config.tool_policy.denied_tools:
            return False
        allowed = config.tool_policy.allowed_tools
        return not allowed or "llm_chat_json" in allowed


class ProjectSecretaryHandler(BaseMvpHandler):
    async def run(self, session: AgentSession, input_payload: Any) -> ProjectStateOutput:
        merged: dict[str, Any] = {"run_id": session.run_id, "project_id": session.project_id}
        if isinstance(input_payload, dict):
            merged.update(input_payload)
        elif input_payload is not None:
            raise AgentRuntimeError("project_secretary expects dict or None input_payload")
        project_input = ProjectStateInput.model_validate(merged)
        manifest = self._expanded_manifest()
        app_token = manifest.base_app_token
        if not app_token.strip():
            raise AgentRuntimeError("base_app_token is empty; set FEISHU_BASE_APP_TOKEN")

        await self._trace(session, "project_secretary_start", project_id=session.project_id)

        proj_table = manifest.tables[BaseTableName.PROJECTS]
        tasks_table = manifest.tables[BaseTableName.TASKS]
        ms_table = manifest.tables[BaseTableName.MILESTONES]

        pr = _unwrap_tool_result(
            await self._tools.call_tool(
                "feishu_bitable_search_records",
                {"app_token": app_token, "table_id": proj_table.table_id, "page_size": 20},
                session,
            )
        )
        tr = _unwrap_tool_result(
            await self._tools.call_tool(
                "feishu_bitable_search_records",
                {"app_token": app_token, "table_id": tasks_table.table_id, "page_size": 100},
                session,
            )
        )
        mr = _unwrap_tool_result(
            await self._tools.call_tool(
                "feishu_bitable_search_records",
                {"app_token": app_token, "table_id": ms_table.table_id, "page_size": 50},
                session,
            )
        )

        proj_items = pr.get("items") or []
        if not proj_items:
            raise AgentRuntimeError("Projects table returned no records")

        today = date.today()
        stale_days = max(1, project_input.time_range_days or manifest.default_time_range_days)

        p0 = proj_items[0].get("fields") or {}
        pname = record_field_text(p0, "项目名称") or "unknown"
        proj_snap = ProjectRecordSnapshot(
            project_record_id=str(proj_items[0].get("record_id") or ""),
            project_name=pname,
            owner=record_field_text(p0, "项目负责人"),
            status=_parse_project_status(record_field_text(p0, "项目状态")),
            priority=_parse_priority(record_field_text(p0, "项目优先级")),
            health=_parse_project_health(record_field_text(p0, "当前健康度")),
            risk_level=_parse_risk_level(record_field_text(p0, "当前风险等级")),
            start_date=record_field_date(p0, "开始日期"),
            target_release_date=record_field_date(p0, "目标上线日期"),
            weekly_progress=record_field_text(p0, "本周关键进展"),
        )

        tasks_out: list[TaskSnapshot] = []
        for row in tr.get("items") or []:
            rid = str(row.get("record_id") or "")
            f = row.get("fields") or {}
            tasks_out.append(
                TaskSnapshot(
                    task_record_id=rid,
                    task_name=record_field_text(f, "任务名称") or rid,
                    owner=record_field_text(f, "负责人"),
                    role_type=record_field_text(f, "角色类型"),
                    status=_parse_task_status(record_field_text(f, "状态")),
                    priority=_parse_priority(record_field_text(f, "优先级", "项目优先级")),
                    due_date=record_field_date(f, "截止时间"),
                    last_updated_at=record_field_date(f, "最近更新时间"),
                    estimated_hours=record_field_number(f, "预计工时"),
                    actual_hours=record_field_number(f, "实际工时"),
                    dependency_task_ids=record_field_texts(f, "依赖任务"),
                    blocking_reason=record_field_text(f, "阻塞说明"),
                    need_followup=record_field_bool(f, "是否需要追问") or False,
                    risk_mark=record_field_text(f, "风险标记"),
                )
            )

        ms_out: list[MilestoneSnapshot] = []
        for row in mr.get("items") or []:
            rid = str(row.get("record_id") or "")
            f = row.get("fields") or {}
            status = _parse_milestone_status(record_field_text(f, "状态"))
            planned = record_field_date(f, "计划日期")
            delay_days = record_field_number(f, "延期天数")
            ms_out.append(
                MilestoneSnapshot(
                    milestone_record_id=rid,
                    milestone_name=record_field_text(f, "里程碑名称") or rid,
                    owner=record_field_text(f, "责任人", "负责人"),
                    planned_date=planned,
                    actual_date=record_field_date(f, "实际日期"),
                    status=status,
                    delay_days=int(delay_days) if delay_days is not None else (max(0, (today - planned).days) if planned and not _milestone_done(status) else None),
                    dependency_summary=record_field_text(f, "依赖说明"),
                    risk_mark=record_field_text(f, "风险标记"),
                )
            )

        missing: list[MissingFieldFinding] = []
        abnormal: list[AbnormalSignal] = []

        for t in tasks_out:
            if not t.owner:
                missing.append(
                    MissingFieldFinding(
                        table_name=BaseTableName.TASKS,
                        record_id=t.task_record_id,
                        field_name="负责人",
                        owner=proj_snap.owner,
                        reason=f"任务「{t.task_name}」缺少负责人，无法确认状态归属",
                        suggested_question=f"请确认任务「{t.task_name}」当前负责人是谁，并补充到任务表。",
                    )
                )
            if not t.role_type:
                missing.append(
                    MissingFieldFinding(
                        table_name=BaseTableName.TASKS,
                        record_id=t.task_record_id,
                        field_name="角色类型",
                        owner=t.owner or proj_snap.owner,
                        reason=f"任务「{t.task_name}」缺少角色类型，影响负责人负载和风险归因",
                        suggested_question=f"请补充任务「{t.task_name}」的角色类型，例如产品/前端/后端/算法/测试/项目经理。",
                    )
                )
            if not t.due_date:
                missing.append(
                    MissingFieldFinding(
                        table_name=BaseTableName.TASKS,
                        record_id=t.task_record_id,
                        field_name="截止时间",
                        owner=t.owner or proj_snap.owner,
                        reason=f"任务「{t.task_name}」缺少截止时间，无法判断是否延期",
                        suggested_question=f"请补充任务「{t.task_name}」的截止时间。",
                    )
                )
            if not t.last_updated_at and not _task_done(t.status):
                missing.append(
                    MissingFieldFinding(
                        table_name=BaseTableName.TASKS,
                        record_id=t.task_record_id,
                        field_name="最近更新时间",
                        owner=t.owner or proj_snap.owner,
                        reason=f"任务「{t.task_name}」缺少最近更新时间，状态可信度不足",
                        suggested_question=f"请补充任务「{t.task_name}」最近一次有效状态更新时间。",
                    )
                )

            if t.status == TaskStatus.BLOCKED:
                abnormal.append(
                    AbnormalSignal(
                        signal_type="task_blocked",
                        table_name=BaseTableName.TASKS,
                        record_id=t.task_record_id,
                        severity="high" if not t.blocking_reason else "medium",
                        description=f"任务阻塞: {t.task_name}" + (f"；阻塞说明：{t.blocking_reason}" if t.blocking_reason else "；缺少阻塞说明"),
                        evidence_refs=[
                            _evidence(
                                table_name=BaseTableName.TASKS,
                                record_id=t.task_record_id,
                                field_name="状态",
                                summary=f"任务状态为 {t.status.value}",
                                value_snapshot=t.status.value,
                            )
                        ],
                    )
                )
                if not t.blocking_reason:
                    missing.append(
                        MissingFieldFinding(
                            table_name=BaseTableName.TASKS,
                            record_id=t.task_record_id,
                            field_name="阻塞说明",
                            owner=t.owner or proj_snap.owner,
                            reason=f"任务「{t.task_name}」状态为阻塞，但没有阻塞说明",
                            suggested_question=f"请说明任务「{t.task_name}」当前阻塞原因、依赖对象和预计解除时间。",
                        )
                    )
                    abnormal.append(
                        AbnormalSignal(
                            signal_type="blocked_reason_missing",
                            table_name=BaseTableName.TASKS,
                            record_id=t.task_record_id,
                            severity="high",
                            description=f"阻塞任务「{t.task_name}」缺少阻塞说明",
                            evidence_refs=[
                                _evidence(
                                    table_name=BaseTableName.TASKS,
                                    record_id=t.task_record_id,
                                    field_name="阻塞说明",
                                    summary="阻塞说明为空",
                                )
                            ],
                        )
                    )

            if t.due_date and t.due_date < today and not _task_done(t.status):
                overdue_days = (today - t.due_date).days
                abnormal.append(
                    AbnormalSignal(
                        signal_type="task_overdue",
                        table_name=BaseTableName.TASKS,
                        record_id=t.task_record_id,
                        severity="high" if t.priority == Priority.P0 or overdue_days >= 3 else "medium",
                        description=f"任务「{t.task_name}」已超过截止时间 {overdue_days} 天，当前状态为 {t.status.value}",
                        evidence_refs=[
                            _evidence(
                                table_name=BaseTableName.TASKS,
                                record_id=t.task_record_id,
                                field_name="截止时间",
                                summary=f"截止时间 {t.due_date.isoformat()}，当前日期 {today.isoformat()}",
                                value_snapshot=t.due_date.isoformat(),
                            )
                        ],
                    )
                )

            if t.last_updated_at and not _task_done(t.status):
                stale = (today - t.last_updated_at).days
                if stale >= stale_days:
                    abnormal.append(
                        AbnormalSignal(
                            signal_type="task_stale",
                            table_name=BaseTableName.TASKS,
                            record_id=t.task_record_id,
                            severity="medium" if stale < stale_days * 2 else "high",
                            description=f"任务「{t.task_name}」已 {stale} 天未更新，超过巡检阈值 {stale_days} 天",
                            evidence_refs=[
                                _evidence(
                                    table_name=BaseTableName.TASKS,
                                    record_id=t.task_record_id,
                                    field_name="最近更新时间",
                                    summary=f"最近更新时间 {t.last_updated_at.isoformat()}",
                                    value_snapshot=t.last_updated_at.isoformat(),
                                )
                            ],
                        )
                    )

            if (
                t.estimated_hours
                and t.actual_hours
                and t.actual_hours > t.estimated_hours * 1.25
                and not _task_done(t.status)
            ):
                abnormal.append(
                    AbnormalSignal(
                        signal_type="task_effort_overrun",
                        table_name=BaseTableName.TASKS,
                        record_id=t.task_record_id,
                        severity="medium",
                        description=f"任务「{t.task_name}」实际工时 {t.actual_hours:g} 已超过预计工时 {t.estimated_hours:g}",
                        evidence_refs=[
                            _evidence(
                                table_name=BaseTableName.TASKS,
                                record_id=t.task_record_id,
                                field_name="实际工时",
                                summary="实际工时超过预计工时 125%",
                                value_snapshot=t.actual_hours,
                            )
                        ],
                    )
                )

        for m in ms_out:
            if not m.owner:
                missing.append(
                    MissingFieldFinding(
                        table_name=BaseTableName.MILESTONES,
                        record_id=m.milestone_record_id,
                        field_name="责任人",
                        owner=proj_snap.owner,
                        reason=f"里程碑「{m.milestone_name}」缺少责任人",
                        suggested_question=f"请确认里程碑「{m.milestone_name}」责任人。",
                    )
                )
            if not m.planned_date:
                missing.append(
                    MissingFieldFinding(
                        table_name=BaseTableName.MILESTONES,
                        record_id=m.milestone_record_id,
                        field_name="计划日期",
                        owner=m.owner or proj_snap.owner,
                        reason=f"里程碑「{m.milestone_name}」缺少计划日期",
                        suggested_question=f"请补充里程碑「{m.milestone_name}」计划日期。",
                    )
                )
            if m.planned_date and m.planned_date < today and not _milestone_done(m.status):
                delay_days = (today - m.planned_date).days
                abnormal.append(
                    AbnormalSignal(
                        signal_type="milestone_delayed",
                        table_name=BaseTableName.MILESTONES,
                        record_id=m.milestone_record_id,
                        severity="high" if delay_days >= 3 or m.status == MilestoneStatus.DELAYED else "medium",
                        description=f"里程碑「{m.milestone_name}」计划日期已过 {delay_days} 天，当前状态为 {m.status.value}",
                        evidence_refs=[
                            _evidence(
                                table_name=BaseTableName.MILESTONES,
                                record_id=m.milestone_record_id,
                                field_name="计划日期",
                                summary=f"计划日期 {m.planned_date.isoformat()}，当前日期 {today.isoformat()}",
                                value_snapshot=m.planned_date.isoformat(),
                            )
                        ],
                    )
                )
            elif m.planned_date and 0 <= (m.planned_date - today).days <= 3 and not _milestone_done(m.status):
                abnormal.append(
                    AbnormalSignal(
                        signal_type="milestone_due_soon",
                        table_name=BaseTableName.MILESTONES,
                        record_id=m.milestone_record_id,
                        severity="medium",
                        description=f"里程碑「{m.milestone_name}」将在 {(m.planned_date - today).days} 天内到期，当前状态为 {m.status.value}",
                        evidence_refs=[
                            _evidence(
                                table_name=BaseTableName.MILESTONES,
                                record_id=m.milestone_record_id,
                                field_name="计划日期",
                                summary=f"临近计划日期 {m.planned_date.isoformat()}",
                                value_snapshot=m.planned_date.isoformat(),
                            )
                        ],
                    )
                )

        if proj_snap.target_release_date and proj_snap.target_release_date < today and proj_snap.status != ProjectStatus.DONE:
            abnormal.append(
                AbnormalSignal(
                    signal_type="project_release_overdue",
                    table_name=BaseTableName.PROJECTS,
                    record_id=proj_snap.project_record_id,
                    severity="high",
                    description=f"项目目标上线日期 {proj_snap.target_release_date.isoformat()} 已过，项目仍未完成",
                    evidence_refs=[
                        _evidence(
                            table_name=BaseTableName.PROJECTS,
                            record_id=proj_snap.project_record_id,
                            field_name="目标上线日期",
                            summary="项目目标上线日期已过",
                            value_snapshot=proj_snap.target_release_date.isoformat(),
                        )
                    ],
                )
            )
        if proj_snap.status in (ProjectStatus.BLOCKED, ProjectStatus.DELAYED):
            abnormal.append(
                AbnormalSignal(
                    signal_type="project_delayed" if proj_snap.status == ProjectStatus.DELAYED else "project_blocked",
                    table_name=BaseTableName.PROJECTS,
                    record_id=proj_snap.project_record_id,
                    severity="high",
                    description=f"项目状态为 {proj_snap.status.value}",
                    evidence_refs=[
                        _evidence(
                            table_name=BaseTableName.PROJECTS,
                            record_id=proj_snap.project_record_id,
                            field_name="项目状态",
                            summary=f"项目状态为 {proj_snap.status.value}",
                            value_snapshot=proj_snap.status.value,
                        )
                    ],
                )
            )
        if proj_snap.health in (ProjectHealth.RISK, ProjectHealth.SEVERE_RISK):
            abnormal.append(
                AbnormalSignal(
                    signal_type="project_health_risk",
                    table_name=BaseTableName.PROJECTS,
                    record_id=proj_snap.project_record_id,
                    severity="high" if proj_snap.health == ProjectHealth.SEVERE_RISK else "medium",
                    description=f"项目当前健康度为 {proj_snap.health.value}",
                    evidence_refs=[
                        _evidence(
                            table_name=BaseTableName.PROJECTS,
                            record_id=proj_snap.project_record_id,
                            field_name="当前健康度",
                            summary=f"项目健康度为 {proj_snap.health.value}",
                            value_snapshot=proj_snap.health.value,
                        )
                    ],
                )
            )

        signal_by_record: dict[str, list[AbnormalSignal]] = {}
        for sig in abnormal:
            signal_by_record.setdefault(sig.record_id, []).append(sig)

        proposed_patches: list[RecordPatch] = []
        for t in tasks_out:
            related_missing = [m for m in missing if m.table_name == BaseTableName.TASKS and m.record_id == t.task_record_id]
            related_signals = signal_by_record.get(t.task_record_id, [])
            if not related_missing and not related_signals:
                continue
            summaries = [m.reason for m in related_missing] + [s.description for s in related_signals]
            proposed_patches.append(
                RecordPatch(
                    table_name=BaseTableName.TASKS,
                    record_id=t.task_record_id,
                    fields={
                        "是否需要追问": bool(related_missing or any(s.severity == "high" for s in related_signals)),
                        "风险标记": "；".join(sorted({s.signal_type for s in related_signals})),
                        "Agent 处理摘要": _compact_lines(summaries, limit=600),
                    },
                    idempotency_key=f"{session.project_id}:Tasks:secretary_patch:{t.task_record_id}:{_iso_week(today)}",
                    reason="项目秘书巡检标记任务风险与追问状态",
                    evidence_refs=[ev for sig in related_signals for ev in sig.evidence_refs],
                )
            )

        summary = (
            f"读取项目「{proj_snap.project_name}」：{len(tasks_out)} 条任务，{len(ms_out)} 条里程碑；"
            f"发现 {len(missing)} 个字段缺口、{len(abnormal)} 个异常信号"
        )
        return ProjectStateOutput(
            run_id=session.run_id,
            project_id=session.project_id,
            agent_name=AgentName.PROJECT_SECRETARY,
            summary=summary,
            project=proj_snap,
            tasks=tasks_out,
            milestones=ms_out,
            missing_fields=missing,
            abnormal_signals=abnormal,
            followup_requests=[m.suggested_question for m in missing if m.suggested_question],
            evidence_refs=[ev for sig in abnormal for ev in sig.evidence_refs],
            proposed_patches=proposed_patches,
        )


def _parse_project_status(text: str | None) -> ProjectStatus:
    if not text:
        return ProjectStatus.IN_PROGRESS
    try:
        return ProjectStatus(text)
    except ValueError:
        return ProjectStatus.IN_PROGRESS


def _parse_task_status(text: str | None) -> TaskStatus:
    if not text:
        return TaskStatus.IN_PROGRESS
    try:
        return TaskStatus(text)
    except ValueError:
        return TaskStatus.IN_PROGRESS


def _parse_milestone_status(text: str | None) -> MilestoneStatus:
    if not text:
        return MilestoneStatus.IN_PROGRESS
    try:
        return MilestoneStatus(text)
    except ValueError:
        return MilestoneStatus.IN_PROGRESS


def _parse_priority(text: str | None) -> Priority:
    if not text:
        return Priority.P1
    try:
        return Priority(text)
    except ValueError:
        return Priority.P1


def _parse_project_health(text: str | None) -> ProjectHealth | None:
    if not text:
        return None
    try:
        return ProjectHealth(text)
    except ValueError:
        return None


def _parse_risk_level(text: str | None) -> RiskLevel | None:
    if not text:
        return None
    try:
        return RiskLevel(text)
    except ValueError:
        return None


class RiskAnalysisHandler(BaseMvpHandler):
    async def run(self, session: AgentSession, input_payload: Any) -> RiskAnalysisOutput:
        raw = _coerce_dict(input_payload)
        raw.setdefault("run_id", session.run_id)
        raw.setdefault("project_id", session.project_id)
        data = RiskAnalysisInput.model_validate(raw)
        await self._trace(session, "risk_analysis_start")

        manifest = self._expanded_manifest()
        app_token = manifest.base_app_token
        risks_table = manifest.tables[BaseTableName.RISKS]

        existing = _unwrap_tool_result(
            await self._tools.call_tool(
                "feishu_bitable_search_records",
                {"app_token": app_token, "table_id": risks_table.table_id, "page_size": 200},
                session,
            )
        )
        existing_keys = _existing_idempotency_keys(existing.get("items") or [])

        tasks_by_id = {task.task_record_id: task for task in data.project_state.tasks}
        milestones_by_id = {m.milestone_record_id: m for m in data.project_state.milestones}
        period = _iso_week()

        candidates: list[RiskCandidate] = []
        seen_keys: set[str] = set()

        def add_candidate(candidate: RiskCandidate) -> None:
            if candidate.idempotency_key in existing_keys or candidate.idempotency_key in seen_keys:
                return
            seen_keys.add(candidate.idempotency_key)
            candidates.append(candidate)

        for sig in data.project_state.abnormal_signals:
            risk_type = _risk_type_from_signal(sig.signal_type)
            risk_level = _risk_level_from_signal(sig.severity)
            task = tasks_by_id.get(sig.record_id)
            milestone = milestones_by_id.get(sig.record_id)
            responsible = task.owner if task else milestone.owner if milestone else data.project_state.project.owner
            related_task_ids = [sig.record_id] if sig.table_name == BaseTableName.TASKS else []
            related_milestone_ids = [sig.record_id] if sig.table_name == BaseTableName.MILESTONES else []
            title_subject = task.task_name if task else milestone.milestone_name if milestone else data.project_state.project.project_name
            needs_followup = sig.signal_type in {
                "task_stale",
                "blocked_reason_missing",
                "project_health_risk",
                "milestone_due_soon",
            }
            add_candidate(
                RiskCandidate(
                    risk_title=f"{risk_type.value}: {title_subject}",
                    risk_type=risk_type,
                    risk_level=risk_level,
                    confidence=0.85 if risk_level == RiskLevel.HIGH else 0.72,
                    trigger_reason=sig.description,
                    responsible_owner=responsible,
                    related_task_ids=related_task_ids,
                    related_milestone_ids=related_milestone_ids,
                    suggested_actions=_suggest_actions(risk_type, risk_level),
                    need_followup=needs_followup or not responsible,
                    followup_target_role=None if responsible else "项目经理",
                    evidence_refs=list(sig.evidence_refs),
                    idempotency_key=f"{session.project_id}:Risks:{risk_type.value}:{sig.record_id}:{period}",
                )
            )

        for mf in data.project_state.missing_fields:
            level = RiskLevel.HIGH if mf.field_name in {"阻塞说明", "负责人", "截止时间"} else RiskLevel.MEDIUM
            ev = _evidence(
                table_name=mf.table_name,
                record_id=mf.record_id,
                field_name=mf.field_name,
                summary=mf.reason,
            )
            add_candidate(
                RiskCandidate(
                    risk_title=f"数据缺失风险: {mf.field_name}",
                    risk_type=RiskType.DATA_MISSING,
                    risk_level=level,
                    confidence=0.78,
                    trigger_reason=mf.reason,
                    responsible_owner=mf.owner,
                    related_task_ids=[mf.record_id] if mf.table_name == BaseTableName.TASKS else [],
                    related_milestone_ids=[mf.record_id] if mf.table_name == BaseTableName.MILESTONES else [],
                    suggested_actions=_suggest_actions(RiskType.DATA_MISSING, level),
                    need_followup=True,
                    followup_target_role="项目经理" if not mf.owner else None,
                    evidence_refs=[ev],
                    idempotency_key=f"{session.project_id}:Risks:{RiskType.DATA_MISSING.value}:{mf.record_id}:{mf.field_name}:{period}",
                )
            )

        candidates = await self._llm_refine_candidates(session, data, candidates)

        high_count = sum(1 for c in candidates if c.risk_level == RiskLevel.HIGH)
        medium_count = sum(1 for c in candidates if c.risk_level == RiskLevel.MEDIUM)
        if high_count:
            health = ProjectHealth.SEVERE_RISK if high_count >= 2 else ProjectHealth.RISK
            risk_level = RiskLevel.HIGH
        elif medium_count:
            health = ProjectHealth.ATTENTION
            risk_level = RiskLevel.MEDIUM
        else:
            health = ProjectHealth.HEALTHY
            risk_level = RiskLevel.LOW

        proposed_creates = [_risk_create(session.project_id, candidate) for candidate in candidates]
        evidence_refs = [ev for candidate in candidates for ev in candidate.evidence_refs]
        requested_evidence = [
            f"{mf.table_name}:{mf.record_id}.{mf.field_name}"
            for mf in data.project_state.missing_fields
        ]
        summary = (
            f"生成 {len(candidates)} 条风险候选（高 {high_count} / 中 {medium_count}），"
            f"跳过已存在幂等键 {len(existing_keys)} 个"
        )
        return RiskAnalysisOutput(
            run_id=session.run_id,
            project_id=session.project_id,
            agent_name=AgentName.RISK_ANALYSIS,
            summary=summary,
            risk_candidates=candidates,
            project_health_recommendation=health,
            project_risk_level_recommendation=risk_level,
            evidence_status="sufficient" if evidence_refs else "insufficient",
            need_more_evidence=bool(requested_evidence) and not any(c.risk_level == RiskLevel.HIGH for c in candidates),
            requested_evidence=requested_evidence,
            evidence_refs=evidence_refs,
            proposed_creates=proposed_creates,
        )

    async def _llm_refine_candidates(
        self,
        session: AgentSession,
        data: RiskAnalysisInput,
        candidates: list[RiskCandidate],
    ) -> list[RiskCandidate]:
        if not candidates or not self._llm_allowed(session):
            return candidates

        out = await self._llm_json(
            session,
            purpose="risk_analysis_refine_candidates",
            instruction=(
                "你是风险识别 Agent。规则引擎已经基于 Base 事实生成风险候选。"
                "请只在现有候选范围内优化风险标题、触发原因、风险等级、建议动作、是否需要追问。"
                "不得新增风险、不得删除风险、不得改幂等键、不得编造 payload 中没有的事实。"
                "必须输出 JSON："
                "{\"risk_candidates\":[{\"idempotency_key\":\"原幂等键\","
                "\"risk_title\":\"标题\", \"risk_level\":\"低|中|高\","
                "\"trigger_reason\":\"基于证据的原因\","
                "\"suggested_actions\":[\"动作\"], \"need_followup\":true,"
                "\"followup_target_role\":\"项目经理或空\"}],"
                "\"project_health\":\"健康|关注|风险|严重风险\","
                "\"project_risk_level\":\"低|中|高\"}。"
            ),
            payload={
                "project": data.project_state.project.model_dump(mode="json"),
                "abnormal_signals": [sig.model_dump(mode="json") for sig in data.project_state.abnormal_signals],
                "missing_fields": [mf.model_dump(mode="json") for mf in data.project_state.missing_fields],
                "rule_candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            },
            max_tokens=2200,
            temperature=0.1,
            required=False,
        )
        rows = out.get("risk_candidates") or out.get("risks") or []
        if not isinstance(rows, list):
            return candidates

        by_key = {
            str(row.get("idempotency_key") or ""): row
            for row in rows
            if isinstance(row, dict)
        }
        refined: list[RiskCandidate] = []
        for candidate in candidates:
            row = by_key.get(candidate.idempotency_key)
            if not isinstance(row, dict):
                refined.append(candidate)
                continue

            updates: dict[str, Any] = {}
            title = str(row.get("risk_title") or "").strip()
            if title:
                updates["risk_title"] = title[:160]
            reason = str(row.get("trigger_reason") or "").strip()
            if reason:
                updates["trigger_reason"] = reason[:900]
            level = _parse_risk_level(str(row.get("risk_level") or ""))
            if level is not None:
                updates["risk_level"] = level
            actions = _str_list(row.get("suggested_actions") or row.get("actions"))
            if actions:
                updates["suggested_actions"] = [action[:180] for action in actions[:5]]
            if isinstance(row.get("need_followup"), bool):
                updates["need_followup"] = bool(row["need_followup"])
            target_role = str(row.get("followup_target_role") or "").strip()
            if target_role:
                updates["followup_target_role"] = target_role[:80]
            responsible_owner = str(row.get("responsible_owner") or "").strip()
            if responsible_owner:
                updates["responsible_owner"] = responsible_owner[:80]
            try:
                confidence = float(row.get("confidence"))
                if 0.0 <= confidence <= 1.0:
                    updates["confidence"] = confidence
            except (TypeError, ValueError):
                pass

            updated = candidate.model_copy(update=updates)
            if updated.risk_level == RiskLevel.HIGH and not updated.suggested_actions:
                updated = updated.model_copy(
                    update={"suggested_actions": _suggest_actions(updated.risk_type, updated.risk_level)}
                )
            refined.append(updated)

        return refined


class FollowUpHandler(BaseMvpHandler):
    async def run(self, session: AgentSession, input_payload: Any) -> FollowUpOutput:
        raw = _coerce_dict(input_payload)
        raw.setdefault("run_id", session.run_id)
        raw.setdefault("project_id", session.project_id)
        data = FollowUpInput.model_validate(raw)
        await self._trace(session, "followup_start")

        manifest = self._expanded_manifest()
        app_token = manifest.base_app_token
        fu_table = manifest.tables[BaseTableName.FOLLOWUPS]

        existing = _unwrap_tool_result(
            await self._tools.call_tool(
                "feishu_bitable_search_records",
                {"app_token": app_token, "table_id": fu_table.table_id, "page_size": 200},
                session,
            )
        )
        existing_keys = _existing_idempotency_keys(existing.get("items") or [])
        period = _iso_week()

        requests: list[FollowUpRequest] = []
        seen_keys: set[str] = set()

        def add_request(request: FollowUpRequest) -> None:
            if request.idempotency_key in existing_keys or request.idempotency_key in seen_keys:
                return
            seen_keys.add(request.idempotency_key)
            requests.append(request)

        for mf in data.missing_fields:
            target_user = mf.owner
            target_role = None if target_user else "项目经理"
            evidence_refs = [
                _evidence(
                    table_name=mf.table_name,
                    record_id=mf.record_id,
                    field_name=mf.field_name,
                    summary=mf.reason,
                )
            ]
            question = mf.suggested_question or f"请补充字段「{mf.field_name}」，并说明当前真实状态。"
            add_request(
                FollowUpRequest(
                    followup_title=f"补齐{mf.field_name}: {mf.record_id}",
                    project_id=session.project_id,
                    related_task_id=mf.record_id if mf.table_name == BaseTableName.TASKS else None,
                    related_risk_id=None,
                    target_user=target_user,
                    target_role=target_role,
                    followup_reason=mf.reason,
                    questions=[question],
                    message=f"{question}\n原因：{mf.reason}",
                    status=FollowUpStatus.TO_SEND,
                    idempotency_key=f"{session.project_id}:FollowUps:missing_{mf.field_name}:{mf.record_id}:{period}",
                    evidence_refs=evidence_refs,
                )
            )

        for risk in data.risk_candidates:
            if not risk.need_followup and risk.risk_level != RiskLevel.HIGH:
                continue
            target_user = risk.responsible_owner
            target_role = risk.followup_target_role or (None if target_user else "项目经理")
            questions = [
                f"请确认风险「{risk.risk_title}」是否属实。",
                "请补充当前阻塞点、预计恢复时间和需要谁协助。",
            ]
            if risk.risk_level == RiskLevel.HIGH:
                questions.append("如果本周无法解除，请说明需要管理层拍板的事项。")
            add_request(
                FollowUpRequest(
                    followup_title=f"确认风险: {risk.risk_title[:40]}",
                    project_id=session.project_id,
                    related_task_id=risk.related_task_ids[0] if risk.related_task_ids else None,
                    related_risk_id=risk.idempotency_key,
                    target_user=target_user,
                    target_role=target_role,
                    followup_reason=risk.trigger_reason,
                    questions=questions,
                    message="\n".join(questions),
                    status=FollowUpStatus.TO_SEND,
                    idempotency_key=f"{session.project_id}:FollowUps:risk_confirm:{risk.idempotency_key}:{period}",
                    evidence_refs=list(risk.evidence_refs),
                )
            )

        requests = await self._llm_refine_followups(session, data, requests)

        proposed_creates = [_followup_create(request) for request in requests]
        summary = f"产出 {len(requests)} 条追问建议（跳过已存在幂等键 {len(existing_keys)} 个，只生成 proposed，不直接发消息）"
        return FollowUpOutput(
            run_id=session.run_id,
            project_id=session.project_id,
            agent_name=AgentName.FOLLOWUP,
            summary=summary,
            followup_requests=requests,
            evidence_refs=[ev for req in requests for ev in req.evidence_refs],
            proposed_creates=proposed_creates,
        )

    async def _llm_refine_followups(
        self,
        session: AgentSession,
        data: FollowUpInput,
        requests: list[FollowUpRequest],
    ) -> list[FollowUpRequest]:
        if not requests or not self._llm_allowed(session):
            return requests

        out = await self._llm_json(
            session,
            purpose="followup_refine_requests",
            instruction=(
                "你是追问 Agent。规则引擎已基于缺失字段和风险候选生成追问草稿。"
                "请只改写现有追问的标题、问题列表和消息文本，让问题更具体、可回复、可闭环。"
                "不得新增追问、不得删除追问、不得改目标人/目标角色/关联记录/幂等键，"
                "不得要求直接发消息或直接写 Base。"
                "必须输出 JSON："
                "{\"followups\":[{\"idempotency_key\":\"原幂等键\","
                "\"followup_title\":\"标题\", \"questions\":[\"问题\"],"
                "\"message\":\"发送给责任人的完整追问文本\"}]}。"
            ),
            payload={
                "missing_fields": [mf.model_dump(mode="json") for mf in data.missing_fields],
                "risk_candidates": [risk.model_dump(mode="json") for risk in data.risk_candidates],
                "rule_followups": [request.model_dump(mode="json") for request in requests],
            },
            max_tokens=2200,
            temperature=0.2,
            required=False,
        )
        rows = out.get("followups") or out.get("followup_requests") or []
        if not isinstance(rows, list):
            return requests

        by_key = {
            str(row.get("idempotency_key") or ""): row
            for row in rows
            if isinstance(row, dict)
        }
        refined: list[FollowUpRequest] = []
        for request in requests:
            row = by_key.get(request.idempotency_key)
            if not isinstance(row, dict):
                refined.append(request)
                continue

            updates: dict[str, Any] = {}
            title = str(row.get("followup_title") or "").strip()
            if title:
                updates["followup_title"] = title[:160]
            questions = _str_list(row.get("questions"))
            if questions:
                updates["questions"] = [question[:240] for question in questions[:5]]
            message = str(row.get("message") or "").strip()
            if message:
                updates["message"] = message[:1200]
            if "questions" in updates and "message" not in updates:
                updates["message"] = "\n".join(updates["questions"])
            if not updates:
                refined.append(request)
                continue
            refined.append(request.model_copy(update=updates))

        return refined


class WeeklyReportHandler(BaseMvpHandler):
    async def run(self, session: AgentSession, input_payload: Any) -> WeeklyReportOutput:
        raw = _coerce_dict(input_payload)
        raw.setdefault("run_id", session.run_id)
        raw.setdefault("project_id", session.project_id)
        data = WeeklyReportInput.model_validate(raw)
        await self._trace(session, "weekly_report_start")

        manifest = self._expanded_manifest()
        app_token = manifest.base_app_token
        wr_table = manifest.tables[BaseTableName.WEEKLY_REPORTS]

        existing = _unwrap_tool_result(
            await self._tools.call_tool(
                "feishu_bitable_search_records",
                {"app_token": app_token, "table_id": wr_table.table_id, "page_size": 100},
                session,
            )
        )
        existing_keys = _existing_idempotency_keys(existing.get("items") or [])
        report_key = f"{session.project_id}:WeeklyReports:weekly_report:{data.period}"

        project = data.project_state.project
        today = date.today()
        next_week = today + timedelta(days=7)

        completed_tasks = [t for t in data.project_state.tasks if t.status == TaskStatus.DONE]
        active_tasks = [t for t in data.project_state.tasks if t.status in {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.WAITING_ACCEPTANCE}]
        blocked_tasks = [t for t in data.project_state.tasks if t.status == TaskStatus.BLOCKED or t.blocking_reason]
        due_soon_tasks = [
            t for t in data.project_state.tasks
            if t.due_date and today <= t.due_date <= next_week and t.status != TaskStatus.DONE
        ]
        due_soon_milestones = [
            m for m in data.project_state.milestones
            if m.planned_date and today <= m.planned_date <= next_week and m.status != MilestoneStatus.DONE
        ]
        high_risks = [r for r in data.risks if r.risk_level == RiskLevel.HIGH]

        progress_items: list[str] = []
        if project.weekly_progress:
            progress_items.append(project.weekly_progress)
        progress_items.append(data.project_state.summary)
        if completed_tasks:
            progress_items.append("已完成任务：" + "、".join(t.task_name for t in completed_tasks[:8]))
        if active_tasks:
            progress_items.append("推进中任务：" + "、".join(f"{t.task_name}({t.status.value})" for t in active_tasks[:8]))

        risk_items = [
            f"[{r.risk_level.value}] {r.risk_title}：{r.trigger_reason}；建议：{'；'.join(r.suggested_actions[:2])}"
            for r in data.risks
        ] or ["本周期未生成新的风险候选。"]

        blocker_items = [
            f"{t.task_name}：{t.blocking_reason or '状态为阻塞但缺少阻塞说明'}"
            for t in blocked_tasks
        ]
        blocker_items.extend(
            f"待追问：{fu.followup_title} -> {fu.target_user or fu.target_role or '未指定对象'}"
            for fu in data.followups[:8]
        )
        if not blocker_items:
            blocker_items.append("未发现明确阻塞项。")

        next_plan_items = [
            f"下周到期任务：{t.task_name}（截止 {t.due_date.isoformat() if t.due_date else '未知'}）"
            for t in due_soon_tasks[:8]
        ]
        next_plan_items.extend(
            f"下周临近里程碑：{m.milestone_name}（计划 {m.planned_date.isoformat() if m.planned_date else '未知'}）"
            for m in due_soon_milestones[:5]
        )
        if not next_plan_items:
            next_plan_items.append("继续推进关键路径任务，并优先闭环本周期追问与高风险事项。")

        decision_items = [
            f"{r.risk_title}：{'; '.join(r.suggested_actions[:3])}"
            for r in high_risks
        ]
        if not decision_items and data.followups:
            decision_items.append("请项目负责人确认未回复追问的处理优先级。")

        report = WeeklyReportDraft(
            period=data.period,
            project_id=session.project_id,
            project_summary=(
                f"{project.project_name} 当前状态：{project.status.value}；"
                f"健康度：{project.health.value if project.health else '未填写'}；"
                f"风险等级：{project.risk_level.value if project.risk_level else '未填写'}。"
            ),
            progress=WeeklyReportSection(
                title="本周进展",
                items=progress_items,
                evidence_refs=list(data.project_state.evidence_refs),
            ),
            risk_summary=WeeklyReportSection(
                title="风险",
                items=risk_items,
                evidence_refs=[ev for risk in data.risks for ev in risk.evidence_refs],
            ),
            blockers=WeeklyReportSection(
                title="阻塞项",
                items=blocker_items,
                evidence_refs=[ev for risk in data.risks if risk.risk_type == RiskType.DEPENDENCY_BLOCK for ev in risk.evidence_refs],
            ),
            next_plan=WeeklyReportSection(
                title="下周计划",
                items=next_plan_items,
                evidence_refs=[
                    _evidence(
                        table_name=BaseTableName.TASKS,
                        record_id=t.task_record_id,
                        field_name="截止时间",
                        summary=f"下周到期任务 {t.task_name}",
                        value_snapshot=t.due_date.isoformat() if t.due_date else None,
                    )
                    for t in due_soon_tasks
                ],
            ),
            decision_items=WeeklyReportSection(
                title="待拍板",
                items=decision_items,
                evidence_refs=[ev for risk in high_risks for ev in risk.evidence_refs],
            ),
            send_status=ReportSendStatus.DRAFT,
        )

        report = await self._llm_refine_report(session, data, report)

        all_evidence = (
            report.progress.evidence_refs
            + report.risk_summary.evidence_refs
            + report.blockers.evidence_refs
            + report.next_plan.evidence_refs
            + report.decision_items.evidence_refs
        )
        proposed_creates = [] if report_key in existing_keys else [_weekly_report_create(report, all_evidence)]
        missing_sections: list[str] = []
        for section_name, section in {
            "本周进展": report.progress,
            "风险摘要": report.risk_summary,
            "阻塞项": report.blockers,
            "下周计划": report.next_plan,
            "待拍板事项": report.decision_items,
        }.items():
            if not section.items and section_name != "待拍板事项":
                missing_sections.append(section_name)

        summary = f"已生成周报草稿（周期 {data.period}），包含 {len(data.risks)} 条风险、{len(data.followups)} 条追问"
        return WeeklyReportOutput(
            run_id=session.run_id,
            project_id=session.project_id,
            agent_name=AgentName.WEEKLY_REPORT,
            summary=summary,
            report=report,
            missing_required_sections=missing_sections,
            evidence_refs=all_evidence,
            proposed_creates=proposed_creates,
        )

    async def _llm_refine_report(
        self,
        session: AgentSession,
        data: WeeklyReportInput,
        report: WeeklyReportDraft,
    ) -> WeeklyReportDraft:
        if not self._llm_allowed(session):
            return report

        out = await self._llm_json(
            session,
            purpose="weekly_report_refine_draft",
            instruction=(
                "你是周报 Agent。规则引擎已基于 Base 事实、风险候选和追问建议生成周报草稿。"
                "请只润色和重组表达，让周报更像管理层 PMO 汇报。"
                "不得添加 payload 中没有的事实、日期、进度、人名或结论。"
                "必须保留结构，输出 JSON："
                "{\"project_summary\":\"项目摘要\","
                "\"progress\":[\"本周进展\"], \"risk_summary\":[\"风险摘要\"],"
                "\"blockers\":[\"阻塞项\"], \"next_plan\":[\"下周计划\"],"
                "\"decision_items\":[\"待拍板事项\"]}。"
            ),
            payload={
                "period": data.period,
                "project_state": data.project_state.model_dump(mode="json"),
                "risks": [risk.model_dump(mode="json") for risk in data.risks],
                "followups": [fu.model_dump(mode="json") for fu in data.followups],
                "rule_report": report.model_dump(mode="json"),
            },
            max_tokens=2600,
            temperature=0.2,
            required=False,
        )
        if not out:
            return report

        updates: dict[str, Any] = {}
        project_summary = str(out.get("project_summary") or "").strip()
        if project_summary:
            updates["project_summary"] = project_summary[:1200]

        section_map = {
            "progress": report.progress,
            "risk_summary": report.risk_summary,
            "blockers": report.blockers,
            "next_plan": report.next_plan,
            "decision_items": report.decision_items,
        }
        for key, section in section_map.items():
            items = _str_list(out.get(key))
            if not items:
                continue
            updates[key] = section.model_copy(update={"items": [item[:260] for item in items[:10]]})

        if not updates:
            return report
        return report.model_copy(update=updates)


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None and str(item)]
    if isinstance(value, str):
        return [value] if value else []
    return [str(value)] if str(value) else []


def _payload_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _project_signal_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, ProjectStateOutput):
        return {"missing_fields": 0, "abnormal_signals": 0, "proposed_creates": 0, "proposed_patches": 0}
    return {
        "missing_fields": len(value.missing_fields),
        "abnormal_signals": len(value.abnormal_signals),
        "proposed_creates": len(value.proposed_creates),
        "proposed_patches": len(value.proposed_patches),
    }


def _risk_signal_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, RiskAnalysisOutput):
        return {
            "risk_candidates": 0,
            "high_risks": 0,
            "needs_followup": 0,
            "need_more_evidence": 0,
            "proposed_creates": 0,
            "proposed_patches": 0,
        }
    return {
        "risk_candidates": len(value.risk_candidates),
        "high_risks": sum(1 for risk in value.risk_candidates if risk.risk_level == RiskLevel.HIGH),
        "needs_followup": sum(1 for risk in value.risk_candidates if risk.need_followup),
        "need_more_evidence": 1 if value.need_more_evidence else 0,
        "proposed_creates": len(value.proposed_creates),
        "proposed_patches": len(value.proposed_patches),
    }


def _followup_signal_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, FollowUpOutput):
        return {"followup_requests": 0, "proposed_creates": 0, "proposed_patches": 0}
    return {
        "followup_requests": len(value.followup_requests),
        "proposed_creates": len(value.proposed_creates),
        "proposed_patches": len(value.proposed_patches),
    }


def _weekly_signal_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, WeeklyReportOutput):
        return {"weekly_reports": 0, "proposed_creates": 0, "proposed_patches": 0}
    return {
        "weekly_reports": 1,
        "proposed_creates": len(value.proposed_creates),
        "proposed_patches": len(value.proposed_patches),
    }


def _has_pending_agent_writes(*outputs: Any) -> bool:
    for output in outputs:
        creates = getattr(output, "proposed_creates", None)
        patches = getattr(output, "proposed_patches", None)
        if creates or patches:
            return True
    return False


def _select_coordinator_next_agent(observation: dict[str, Any]) -> tuple[AgentName | None, str]:
    changed_tables = set(_str_list(observation.get("changed_tables")))
    force_full_chain = bool(observation.get("force_full_chain"))
    wants_weekly = bool(observation.get("wants_weekly"))

    if not observation["has_project_state"]:
        if changed_tables:
            reason = f"先由项目秘书读取 Base 最新状态；本次变化表：{','.join(sorted(changed_tables))}"
        else:
            reason = "先由项目秘书读取 Projects/Tasks/Milestones，建立本轮观察"
        return AgentName.PROJECT_SECRETARY, reason

    project_counts = observation.get("project_signal_counts") or {}
    risk_counts = observation.get("risk_signal_counts") or {}
    has_project_signals = (
        int(project_counts.get("missing_fields", 0)) > 0
        or int(project_counts.get("abnormal_signals", 0)) > 0
    )
    needs_risk_analysis = force_full_chain or wants_weekly or has_project_signals
    if needs_risk_analysis and not observation["has_risk_output"]:
        if force_full_chain:
            return AgentName.RISK_ANALYSIS, "演示模式要求完整展示风险识别 Agent"
        if wants_weekly:
            return AgentName.RISK_ANALYSIS, "周报需要先获得风险画像和证据状态"
        return AgentName.RISK_ANALYSIS, "项目秘书发现异常或字段缺失，交给风险识别 Agent 判断风险候选"

    has_followup_need = (
        force_full_chain
        or wants_weekly
        or int(project_counts.get("missing_fields", 0)) > 0
        or int(risk_counts.get("needs_followup", 0)) > 0
        or int(risk_counts.get("need_more_evidence", 0)) > 0
    )
    if has_followup_need and not observation["has_followup_output"]:
        if force_full_chain:
            return AgentName.FOLLOWUP, "演示模式要求完整展示追问 Agent"
        if wants_weekly:
            return AgentName.FOLLOWUP, "周报前需要整理追问闭环和待补信息"
        return AgentName.FOLLOWUP, "存在缺失字段或风险证据不足，需要追问 Agent 生成具体追问"

    if wants_weekly and not observation["has_weekly_output"]:
        return AgentName.WEEKLY_REPORT, "本次触发要求生成周报，交给周报 Agent 汇总当前证据"

    return None, "当前观察没有需要继续委派的子 Agent"


def _coordinator_agent_choice_allowed(agent: AgentName, observation: dict[str, Any]) -> bool:
    if agent == AgentName.PROJECT_SECRETARY:
        return not observation["has_project_state"]
    if agent == AgentName.RISK_ANALYSIS:
        return observation["has_project_state"] and not observation["has_risk_output"]
    if agent == AgentName.FOLLOWUP:
        return observation["has_project_state"] and observation["has_risk_output"] and not observation["has_followup_output"]
    if agent == AgentName.WEEKLY_REPORT:
        return (
            observation["has_project_state"]
            and observation["has_risk_output"]
            and observation["has_followup_output"]
            and not observation["has_weekly_output"]
        )
    return False


def _coordinator_noop_reason(observation: dict[str, Any]) -> str:
    if not observation.get("writeback", True):
        return "当前运行关闭写回，Coordinator 在完成必要观察后停止"
    if not observation.get("has_pending_writes"):
        project_counts = observation.get("project_signal_counts") or {}
        return (
            "当前 Base 观察没有产生需要写回的风险、追问、周报或项目状态更新；"
            f"异常 {project_counts.get('abnormal_signals', 0)} 个，缺失字段 {project_counts.get('missing_fields', 0)} 个"
        )
    return "当前没有更多可执行动作"


class CoordinatorHandler(BaseMvpHandler):
    def __init__(
        self,
        tool_executor: ToolExecutorProtocol,
        project_root: Any,
        quality_gate: QualityGateProtocol,
    ) -> None:
        super().__init__(tool_executor, project_root)
        self._quality_gate = quality_gate
        self._runtime: Any | None = None
        self._remote_fields_cache: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._project_record_cache: dict[str, str] = {}

    def bind_runtime(self, runtime: Any) -> None:
        self._runtime = runtime

    async def observe(self, context: AgentContext, input_payload: Any) -> dict[str, Any]:
        payload = _coerce_dict(input_payload)
        state = context.loop_state
        has_write_bundle = bool(payload.get("proposed_creates") or payload.get("proposed_patches"))
        mode = "writeback_only" if has_write_bundle and not payload.get("orchestrate") else "full_chain"
        loop_data = state.data if state is not None else {}
        completed_agents = [
            str(agent)
            for agent in loop_data.get("called_agents", [])
        ]
        project_state = loop_data.get("project_state")
        risk_out = loop_data.get("risk_out")
        follow_out = loop_data.get("follow_out")
        weekly_out = loop_data.get("weekly_out")
        changed_tables = _str_list(payload.get("changed_tables") or payload.get("watch_tables"))
        force_full_chain = _payload_bool(payload, "force_full_chain") or _payload_bool(payload, "demo_full_chain")
        wants_weekly = (
            force_full_chain
            or _payload_bool(payload, "generate_weekly_report")
            or str(context.session.trigger_type) == "weekly_report"
        )
        return {
            "mode": mode,
            "writeback": True if "writeback" not in payload else _payload_bool(payload, "writeback"),
            "period": payload.get("period") or _iso_week(),
            "project_id": context.session.project_id,
            "trigger_type": str(context.session.trigger_type),
            "changed_tables": changed_tables,
            "changed_record_ids": _str_list(payload.get("changed_record_ids")),
            "change_summary": payload.get("change_summary") if isinstance(payload.get("change_summary"), dict) else {},
            "force_full_chain": force_full_chain,
            "wants_weekly": wants_weekly,
            "payload": payload,
            "loop_iteration": state.iteration if state is not None else 0,
            "completed_agents": completed_agents,
            "has_project_state": "project_state" in loop_data,
            "has_risk_output": "risk_out" in loop_data,
            "has_followup_output": "follow_out" in loop_data,
            "has_weekly_output": "weekly_out" in loop_data,
            "has_writeback_output": "write_plan" in loop_data,
            "project_signal_counts": _project_signal_counts(project_state),
            "risk_signal_counts": _risk_signal_counts(risk_out),
            "followup_signal_counts": _followup_signal_counts(follow_out),
            "weekly_signal_counts": _weekly_signal_counts(weekly_out),
            "has_pending_writes": _has_pending_agent_writes(project_state, risk_out, follow_out, weekly_out),
            "context_snapshot": context.snapshot(),
        }

    async def think(self, context: AgentContext, observation: dict[str, Any]) -> CoordinatorPlan:
        actions: list[CoordinatorAction] = []
        if observation["mode"] == "writeback_only":
            actions.append(
                CoordinatorAction(
                    action_type=ActionType.CREATE_RECORD,
                    target_table=None,
                    reason="处理外部提交的 proposed_creates/proposed_patches 写回包",
                )
            )
            called_agents: list[AgentName] = []
        else:
            called_agents = [AgentName(value) for value in observation.get("completed_agents", [])]
            next_agent, reason = _select_coordinator_next_agent(observation)
            next_agent, reason = await self._llm_select_next_agent(
                context.session,
                observation,
                fallback_agent=next_agent,
                fallback_reason=reason,
            )

            if next_agent is not None:
                actions.append(
                    CoordinatorAction(
                        action_type=ActionType.CALL_AGENT,
                        target_agent=next_agent,
                        reason=reason,
                        require_quality_gate=False,
                    )
                )
            elif observation["writeback"] and not observation["has_writeback_output"] and observation["has_pending_writes"]:
                actions.append(
                    CoordinatorAction(
                        action_type=ActionType.CREATE_RECORD,
                        reason="汇总当前已产生的 proposed writes 和 AgentRuns，经 QualityGate 写回 Base",
                    )
                )
            else:
                actions.append(
                    CoordinatorAction(
                        action_type=ActionType.NOOP,
                        reason=_coordinator_noop_reason(observation),
                        require_quality_gate=False,
                    )
                )
        return CoordinatorPlan(
            run_id=context.session.run_id,
            project_id=context.session.project_id,
            decision="agent_loop_decision" if observation["mode"] != "writeback_only" else observation["mode"],
            called_agents=called_agents,
            evidence_status="unknown",
            actions=actions,
            stop_reason=None,
        )

    async def _llm_select_next_agent(
        self,
        session: AgentSession,
        observation: dict[str, Any],
        *,
        fallback_agent: AgentName | None,
        fallback_reason: str,
    ) -> tuple[AgentName | None, str]:
        compact_observation = {
            key: value
            for key, value in observation.items()
            if key not in {"payload", "context_snapshot"}
        }
        out = await self._llm_json(
            session,
            purpose="coordinator_think_select_next_agent",
            instruction=(
                "你是 PMO Coordinator Agent。请你基于 observation 决定本轮下一步应该委派哪个 Agent。"
                "必须输出 JSON："
                "{\"next_agent\":\"project_secretary|risk_analysis|followup|weekly_report|none\","
                "\"reason\":\"一句中文理由\", \"confidence\":0.0到1.0}。"
                "如果缺少 project_state，只能选择 project_secretary。"
                "如果风险/追问/周报的前置输出还不存在，不得跳过前置 Agent。"
                "如果没有必要继续委派，选择 none。"
            ),
            payload={
                "observation": compact_observation,
                "rule_fallback_for_safety": {
                    "next_agent": str(fallback_agent) if fallback_agent else "none",
                    "reason": fallback_reason,
                },
            },
            max_tokens=800,
            temperature=0.0,
            required=not _env_bool("HIVEMIND_COORDINATOR_LLM_OPTIONAL"),
        )
        raw_choice = str(out.get("next_agent") or "").strip()
        llm_reason = str(out.get("reason") or "").strip() or "LLM 未给出理由"
        mapping = {
            "project_secretary": AgentName.PROJECT_SECRETARY,
            "risk_analysis": AgentName.RISK_ANALYSIS,
            "followup": AgentName.FOLLOWUP,
            "weekly_report": AgentName.WEEKLY_REPORT,
        }
        if raw_choice in {"none", "noop", "finish", ""}:
            return None, f"LLM决策：{llm_reason}"
        selected = mapping.get(raw_choice)
        if selected is not None and _coordinator_agent_choice_allowed(selected, observation):
            return selected, f"LLM决策：{llm_reason}"
        return fallback_agent, f"LLM建议 {raw_choice or '<empty>'} 不满足前置约束，规则兜底：{fallback_reason}"

    async def act(self, context: AgentContext, plan: CoordinatorPlan, input_payload: Any) -> CoordinatorPlan:
        payload = _coerce_dict(input_payload)
        state = context.loop_state
        if state is None:
            raise AgentRuntimeError("Coordinator requires AgentLoopState")
        if plan.decision == "writeback_only":
            write_plan = await self._writeback(context.session, payload, called_agents=plan.called_agents, actions=plan.actions)
            state.data["write_plan"] = write_plan
            state.finish(write_plan, reason=write_plan.stop_reason)
            return write_plan
        if self._runtime is None:
            raise AgentRuntimeError("Coordinator runtime is not bound; cannot orchestrate child agents")

        action = plan.actions[0] if plan.actions else None
        if action is None:
            final = self._no_write_final_plan(context, plan)
            state.finish(final, reason=final.stop_reason)
            return final

        if action.action_type == ActionType.CALL_AGENT:
            if action.target_agent is None:
                raise AgentRuntimeError("CALL_AGENT action missing target_agent")
            return await self._act_call_child_agent(context, payload, plan, action.target_agent)

        if action.action_type == ActionType.CREATE_RECORD:
            write_plan = await self._act_writeback(context, plan)
            state.data["write_plan"] = write_plan
            state.finish(write_plan, reason=write_plan.stop_reason)
            return write_plan

        final = self._final_plan(context, plan, decision="agent_loop_noop")
        state.finish(final, reason=final.stop_reason)
        return final

    async def _act_call_child_agent(
        self,
        context: AgentContext,
        payload: dict[str, Any],
        plan: CoordinatorPlan,
        target_agent: AgentName,
    ) -> CoordinatorPlan:
        state = context.loop_state
        if state is None or self._runtime is None:
            raise AgentRuntimeError("Coordinator loop state/runtime missing")

        child_event = AgentTriggerEvent(
            event_id=str(uuid4()),
            event_type=EventType.RUN_FULL_DEMO_CHAIN,
            trigger_type=context.session.trigger_type,
            project_id=context.session.project_id,
            trigger_user=context.session.trigger_user,
            metadata={"parent_run_id": context.session.run_id},
        )

        if target_agent == AgentName.PROJECT_SECRETARY:
            child_session = await self._runtime.run_agent(
                AgentCallRequest(
                    parent_run_id=context.session.run_id,
                    agent_name=AgentName.PROJECT_SECRETARY,
                    event=child_event,
                    reason="coordinator_observe_project_state",
                    input_payload={},
                )
            )
            self._ensure_child_success(child_session)
            project_state = ProjectStateOutput.model_validate(_session_output(child_session, AgentName.PROJECT_SECRETARY) or {})
            state.data["project_state"] = project_state
            context.add_output(project_state, summary=project_state.summary)
            output_summary = project_state.summary

        elif target_agent == AgentName.RISK_ANALYSIS:
            project_state = state.data.get("project_state")
            if not isinstance(project_state, ProjectStateOutput):
                raise AgentRuntimeError("RiskAnalysis requires project_state from project_secretary")
            child_session = await self._runtime.run_agent(
                AgentCallRequest(
                    parent_run_id=context.session.run_id,
                    agent_name=AgentName.RISK_ANALYSIS,
                    event=child_event,
                    reason="coordinator_analyze_risk",
                    input_payload=RiskAnalysisInput(
                        run_id=str(state.data.get("project_secretary_run_id") or context.session.run_id),
                        project_id=context.session.project_id,
                        project_state=project_state,
                    ).model_dump(mode="json"),
                )
            )
            self._ensure_child_success(child_session)
            risk_out = RiskAnalysisOutput.model_validate(_session_output(child_session, AgentName.RISK_ANALYSIS) or {})
            state.data["risk_out"] = risk_out
            context.add_output(risk_out, summary=risk_out.summary)
            output_summary = risk_out.summary

        elif target_agent == AgentName.FOLLOWUP:
            project_state = state.data.get("project_state")
            risk_out = state.data.get("risk_out")
            if not isinstance(project_state, ProjectStateOutput) or not isinstance(risk_out, RiskAnalysisOutput):
                raise AgentRuntimeError("FollowUp requires project_state and risk_out")
            child_session = await self._runtime.run_agent(
                AgentCallRequest(
                    parent_run_id=context.session.run_id,
                    agent_name=AgentName.FOLLOWUP,
                    event=child_event,
                    reason="coordinator_generate_followups",
                    input_payload=FollowUpInput(
                        run_id=str(state.data.get("risk_analysis_run_id") or context.session.run_id),
                        project_id=context.session.project_id,
                        missing_fields=project_state.missing_fields,
                        risk_candidates=risk_out.risk_candidates,
                    ).model_dump(mode="json"),
                )
            )
            self._ensure_child_success(child_session)
            follow_out = FollowUpOutput.model_validate(_session_output(child_session, AgentName.FOLLOWUP) or {})
            state.data["follow_out"] = follow_out
            context.add_output(follow_out, summary=follow_out.summary)
            output_summary = follow_out.summary

        elif target_agent == AgentName.WEEKLY_REPORT:
            project_state = state.data.get("project_state")
            risk_out = state.data.get("risk_out")
            follow_out = state.data.get("follow_out")
            if (
                not isinstance(project_state, ProjectStateOutput)
                or not isinstance(risk_out, RiskAnalysisOutput)
                or not isinstance(follow_out, FollowUpOutput)
            ):
                raise AgentRuntimeError("WeeklyReport requires project_state, risk_out and follow_out")
            period = str(payload.get("period") or _iso_week())
            child_session = await self._runtime.run_agent(
                AgentCallRequest(
                    parent_run_id=context.session.run_id,
                    agent_name=AgentName.WEEKLY_REPORT,
                    event=child_event,
                    reason="coordinator_generate_weekly_report",
                    input_payload=WeeklyReportInput(
                        run_id=str(state.data.get("followup_run_id") or context.session.run_id),
                        project_id=context.session.project_id,
                        period=period,
                        project_state=project_state,
                        risks=risk_out.risk_candidates,
                        followups=follow_out.followup_requests,
                    ).model_dump(mode="json"),
                )
            )
            self._ensure_child_success(child_session)
            weekly_out = WeeklyReportOutput.model_validate(_session_output(child_session, AgentName.WEEKLY_REPORT) or {})
            state.data["weekly_out"] = weekly_out
            context.add_output(weekly_out, summary=weekly_out.summary)
            output_summary = weekly_out.summary

        else:
            raise AgentRuntimeError(f"Unsupported coordinator child agent: {target_agent}")

        state.data[f"{target_agent.value}_run_id"] = child_session.run_id
        state.data.setdefault("child_sessions", []).append(child_session)
        called_agents = list(state.data.setdefault("called_agents", []))
        if target_agent not in called_agents:
            called_agents.append(target_agent)
            state.data["called_agents"] = called_agents

        return CoordinatorPlan(
            run_id=context.session.run_id,
            project_id=context.session.project_id,
            decision=f"called_{target_agent.value}",
            called_agents=called_agents,
            evidence_status="unknown",
            actions=plan.actions,
            stop_reason=output_summary,
        )

    async def _act_writeback(self, context: AgentContext, plan: CoordinatorPlan) -> CoordinatorPlan:
        state = context.loop_state
        if state is None:
            raise AgentRuntimeError("Coordinator loop state missing")
        project_state = state.data.get("project_state")
        risk_out = state.data.get("risk_out")
        follow_out = state.data.get("follow_out")
        weekly_out = state.data.get("weekly_out")
        proposed_creates: list[dict[str, Any]] = []
        proposed_patches: list[dict[str, Any]] = []
        if isinstance(risk_out, RiskAnalysisOutput):
            proposed_creates.extend(item.model_dump(mode="json") for item in risk_out.proposed_creates)
        if isinstance(follow_out, FollowUpOutput):
            proposed_creates.extend(item.model_dump(mode="json") for item in follow_out.proposed_creates)
        if isinstance(weekly_out, WeeklyReportOutput):
            proposed_creates.extend(item.model_dump(mode="json") for item in weekly_out.proposed_creates)
        proposed_creates.extend(
            _agent_run_create(child, "coordinator_child_agent_run").model_dump(mode="json")
            for child in state.data.get("child_sessions", [])
        )
        proposed_creates.append(
            _agent_run_create(context.session, "coordinator_orchestration_run", status_override="running").model_dump(mode="json")
        )
        if isinstance(project_state, ProjectStateOutput):
            proposed_patches.extend(item.model_dump(mode="json") for item in project_state.proposed_patches)

        write_plan = await self._writeback(
            context.session,
            {
                "proposed_creates": proposed_creates,
                "proposed_patches": proposed_patches,
            },
            called_agents=list(state.data.get("called_agents", [])),
            actions=plan.actions,
        )
        write_plan.decision = "agent_loop_writeback"
        return write_plan

    def _no_write_final_plan(self, context: AgentContext, plan: CoordinatorPlan) -> CoordinatorPlan:
        return self._final_plan(context, plan, decision="full_chain_no_writeback")

    def _final_plan(self, context: AgentContext, plan: CoordinatorPlan, *, decision: str) -> CoordinatorPlan:
        state = context.loop_state
        project_state = state.data.get("project_state") if state is not None else None
        risk_out = state.data.get("risk_out") if state is not None else None
        follow_out = state.data.get("follow_out") if state is not None else None
        weekly_out = state.data.get("weekly_out") if state is not None else None
        counts = {
            "project": _project_signal_counts(project_state),
            "risk": _risk_signal_counts(risk_out),
            "followup": _followup_signal_counts(follow_out),
            "weekly": _weekly_signal_counts(weekly_out),
        }
        called = list(state.data.get("called_agents", [])) if state is not None else plan.called_agents
        summary = (
            f"Agent loop 完成：已调用 {','.join(str(a) for a in called) or '无子 Agent'}；"
            f"异常 {counts['project']['abnormal_signals']} 个、缺失字段 {counts['project']['missing_fields']} 个、"
            f"风险 {counts['risk']['risk_candidates']} 条、追问 {counts['followup']['followup_requests']} 条、"
            f"周报 {counts['weekly']['weekly_reports']} 份；未执行业务写回"
        )
        return CoordinatorPlan(
            run_id=context.session.run_id,
            project_id=context.session.project_id,
            decision=decision,
            called_agents=called,
            evidence_status=risk_out.evidence_status if isinstance(risk_out, RiskAnalysisOutput) else "unknown",
            actions=plan.actions,
            stop_reason=summary,
        )

    async def run(self, session: AgentSession, input_payload: Any) -> CoordinatorPlan:
        return await self._writeback(session, _coerce_dict(input_payload), called_agents=[], actions=[])

    async def should_continue(
        self,
        context: AgentContext,
        state: Any,
        observation: Any,
        thought: Any,
        output: Any,
        verify_result: Any,
    ) -> bool:
        if state.finished or state.blocked:
            return False
        if isinstance(output, CoordinatorPlan) and output.decision in {
            "agent_loop_writeback",
            "agent_loop_noop",
            "full_chain_no_writeback",
            "mvp_coordinator_chain",
        }:
            state.finish(output, reason=output.stop_reason)
            return False
        return state.can_continue

    async def finalize(self, context: AgentContext, state: Any, output: Any) -> CoordinatorPlan:
        if isinstance(state.final_output, CoordinatorPlan):
            return state.final_output
        if isinstance(output, CoordinatorPlan):
            return output
        return CoordinatorPlan(
            run_id=context.session.run_id,
            project_id=context.session.project_id,
            decision="blocked",
            called_agents=list(state.data.get("called_agents", [])),
            evidence_status="unknown",
            actions=[],
            stop_reason=state.stop_reason or "Coordinator loop ended without final CoordinatorPlan",
        )

    async def _writeback(
        self,
        session: AgentSession,
        payload: dict[str, Any],
        *,
        called_agents: list[AgentName],
        actions: list[CoordinatorAction],
    ) -> CoordinatorPlan:
        await self._trace(session, "coordinator_start")

        manifest = self._expanded_manifest()
        app_token = manifest.base_app_token

        proposed = payload.get("proposed_creates") or []
        proposed_patches = payload.get("proposed_patches") or []

        creates = [RecordCreate.model_validate(x) for x in proposed] if proposed else []
        patches = [RecordPatch.model_validate(x) for x in proposed_patches] if proposed_patches else []

        if creates or patches:
            qreq = QualityGateRequest(
                run_id=session.run_id,
                project_id=session.project_id,
                action_type=ActionType.CREATE_RECORD if creates else ActionType.UPDATE_RECORD,
                payload=payload,
                proposed_creates=creates,
                proposed_patches=patches,
                evidence_refs=[ev for pc in creates for ev in pc.evidence_refs] + [ev for pp in patches for ev in pp.evidence_refs],
                schema_name="CoordinatorWriteBundle",
            )
            qres = await self._quality_gate.verify(qreq)
            if not qres.passed:
                failed = [c for c in (qres.checks or []) if not c.passed]
                details = "; ".join(f"{c.check_name}={c.reason or 'failed'}" for c in failed[:8])
                message = qres.blocked_reason or "quality gate blocked"
                if details:
                    message = f"{message}: {details}"
                raise AgentRuntimeError(message)

            for pc in creates:
                if await self._idempotent_create_exists(session, manifest, app_token, pc):
                    session.output_records.append(
                        OutputRecordRef(
                            table_name=pc.table_name,
                            record_id=pc.idempotency_key or "",
                            operation="skipped",
                            summary=f"幂等跳过：{pc.reason or pc.table_name}",
                        )
                    )
                    continue
                table_id = manifest.tables[pc.table_name].table_id
                fields = await self._prepare_fields_for_write(session, manifest, app_token, pc.table_name, pc.fields)
                cr_out = await self._tools.call_tool(
                    "feishu_bitable_create_record",
                    {"app_token": app_token, "table_id": table_id, "fields": fields},
                    session,
                )
                res = _unwrap_tool_result(cr_out)
                session.output_records.append(
                    OutputRecordRef(
                        table_name=pc.table_name,
                        record_id=str(res.get("record_id", "")),
                        operation="created",
                        summary=pc.reason,
                    )
                )

            for patch in patches:
                table_id = manifest.tables[patch.table_name].table_id
                fields = await self._prepare_fields_for_write(session, manifest, app_token, patch.table_name, patch.fields)
                up_out = await self._tools.call_tool(
                    "feishu_bitable_update_record",
                    {
                        "app_token": app_token,
                        "table_id": table_id,
                        "record_id": patch.record_id,
                        "fields": fields,
                    },
                    session,
                )
                res = _unwrap_tool_result(up_out)
                session.output_records.append(
                    OutputRecordRef(
                        table_name=patch.table_name,
                        record_id=str(res.get("record_id") or patch.record_id),
                        operation="updated",
                        summary=patch.reason,
                    )
                )

        created = sum(1 for item in session.output_records if item.operation == "created")
        updated = sum(1 for item in session.output_records if item.operation == "updated")
        skipped = sum(1 for item in session.output_records if item.operation == "skipped")
        summary = (
            "协调完成"
            + (f"，创建 {created} 条、更新 {updated} 条、幂等跳过 {skipped} 条" if creates or patches else "（无写回）")
        )
        return CoordinatorPlan(
            run_id=session.run_id,
            project_id=session.project_id,
            decision="mvp_coordinator_chain",
            called_agents=called_agents,
            evidence_status="sufficient",
            actions=actions,
            stop_reason=summary,
        )

    async def _remote_fields(
        self,
        session: AgentSession,
        app_token: str,
        table_id: str,
    ) -> dict[str, dict[str, Any]]:
        cache_key = (app_token, table_id)
        cached = self._remote_fields_cache.get(cache_key)
        if cached is not None:
            return cached

        out = _unwrap_tool_result(
            await self._tools.call_tool(
                "feishu_bitable_list_fields",
                {
                    "app_token": app_token,
                    "table_id": table_id,
                    "page_size": 500,
                },
                session,
            )
        )
        fields: dict[str, dict[str, Any]] = {}
        for item in out.get("items") or []:
            if isinstance(item, dict):
                name = _field_name(item)
                if name:
                    fields[name] = item
        self._remote_fields_cache[cache_key] = fields
        return fields

    async def _project_record_id(
        self,
        session: AgentSession,
        manifest: ProjectManifest,
        app_token: str,
    ) -> str:
        cached = self._project_record_cache.get(session.project_id)
        if cached:
            return cached

        table = manifest.tables[BaseTableName.PROJECTS]
        out = _unwrap_tool_result(
            await self._tools.call_tool(
                "feishu_bitable_search_records",
                {"app_token": app_token, "table_id": table.table_id, "page_size": 20},
                session,
            )
        )
        items = [item for item in (out.get("items") or []) if isinstance(item, dict)]
        if not items:
            raise AgentRuntimeError("Projects table returned no records; cannot resolve link field 所属项目")

        selected = items[0]
        for item in items:
            fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
            project_name = record_field_text(fields, "项目名称")
            if project_name in {manifest.project_name, session.project_id}:
                selected = item
                break
        record_id = str(selected.get("record_id") or "")
        if not record_id:
            raise AgentRuntimeError("Projects table record is missing record_id; cannot resolve link field 所属项目")
        self._project_record_cache[session.project_id] = record_id
        return record_id

    async def _coerce_field_value(
        self,
        session: AgentSession,
        manifest: ProjectManifest,
        app_token: str,
        field_name: str,
        field_meta: dict[str, Any],
        value: Any,
    ) -> Any:
        field_type = _field_type(field_meta)

        if field_type == 18:
            record_ids = _record_ids_from_value(value)
            if not record_ids and field_name == "所属项目":
                record_ids = [await self._project_record_id(session, manifest, app_token)]
            return record_ids

        if field_type == 11:
            ids = _record_ids_from_value(value)
            return ids

        if field_type == 7:
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "y", "是", "需要", "已升级"}
            return bool(value)

        if field_type == 15:
            if value is None:
                return None
            if isinstance(value, dict):
                return value
            text = str(value).strip()
            if not text:
                return None
            return {"link": text, "text": text}

        if field_type == 1 and not isinstance(value, str):
            if value is None:
                return ""
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False)
            return str(value)

        if field_type == 2 and isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return value

        return value

    async def _prepare_fields_for_write(
        self,
        session: AgentSession,
        manifest: ProjectManifest,
        app_token: str,
        table_name: BaseTableName,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        table = manifest.tables[table_name]
        remote_fields = await self._remote_fields(session, app_token, table.table_id)
        actual_names = set(remote_fields)

        expanded = dict(fields)
        if table_name == BaseTableName.RISKS and "ID" in actual_names and "ID" not in expanded and "风险标题" in expanded:
            expanded["ID"] = expanded["风险标题"]
        if table_name == BaseTableName.FOLLOWUPS and "ID" in actual_names and "ID" not in expanded and "追问标题" in expanded:
            expanded["ID"] = expanded["追问标题"]
        if table_name == BaseTableName.WEEKLY_REPORTS and "ID" in actual_names and "ID" not in expanded and "周期" in expanded:
            expanded["ID"] = expanded["周期"]
        prepared: dict[str, Any] = {}
        missing: list[str] = []
        alias_notes: list[str] = []
        aliases = _FIELD_ALIASES.get(table_name, {})

        for desired_name, value in expanded.items():
            actual_name = desired_name if desired_name in actual_names else ""
            if not actual_name:
                for alias in aliases.get(desired_name, ()):
                    if alias in actual_names:
                        actual_name = alias
                        alias_notes.append(f"{desired_name}->{alias}")
                        break
            if not actual_name:
                missing.append(desired_name)
                continue
            coerced = await self._coerce_field_value(
                session,
                manifest,
                app_token,
                actual_name,
                remote_fields[actual_name],
                value,
            )
            if coerced is None and _field_type(remote_fields[actual_name]) == 15:
                continue
            prepared[actual_name] = coerced

        if missing:
            actual_preview = ", ".join(sorted(actual_names)) or "<empty>"
            missing_preview = ", ".join(missing)
            raise AgentRuntimeError(
                "Feishu Base schema mismatch before writeback: "
                f"table={table_name.value} table_id={table.table_id} missing_fields=[{missing_preview}]. "
                f"actual_fields=[{actual_preview}]. "
                "Fix: run `uv run python scripts/ensure_bitable_fields.py --project-id "
                f"{session.project_id} --tables {table_name.value} --yes` or add the fields manually in Feishu Base."
            )

        if alias_notes:
            await self._trace(
                session,
                "coordinator_field_alias_applied",
                table_name=table_name.value,
                aliases=alias_notes,
            )
        return prepared

    def _ensure_child_success(self, session: AgentSession) -> None:
        if session.status.value != "success":
            detail = "; ".join(error.message for error in session.errors)
            raise AgentRuntimeError(f"Child agent {session.agent_name} failed: {detail or session.status.value}")

    async def _idempotent_create_exists(
        self,
        session: AgentSession,
        manifest: ProjectManifest,
        app_token: str,
        create: RecordCreate,
    ) -> bool:
        key = create.idempotency_key or str(create.fields.get("幂等键") or "")
        if not key:
            return False
        table_id = manifest.tables[create.table_name].table_id
        out = _unwrap_tool_result(
            await self._tools.call_tool(
                "feishu_bitable_search_records",
                {
                    "app_token": app_token,
                    "table_id": table_id,
                    "page_size": 500,
                },
                session,
            )
        )
        return key in _existing_idempotency_keys(out.get("items") or [])


class MVPAgentRegistry:
    def __init__(
        self,
        runtime_config: Any,
        tool_executor: ToolExecutorProtocol,
        project_root: Any,
        quality_gate: QualityGateProtocol,
    ) -> None:
        from agent_runtime.config import RuntimeConfig

        self._cfg: RuntimeConfig = runtime_config
        self._handlers = {
            AgentName.PROJECT_SECRETARY: ProjectSecretaryHandler(tool_executor, project_root),
            AgentName.RISK_ANALYSIS: RiskAnalysisHandler(tool_executor, project_root),
            AgentName.FOLLOWUP: FollowUpHandler(tool_executor, project_root),
            AgentName.WEEKLY_REPORT: WeeklyReportHandler(tool_executor, project_root),
            AgentName.COORDINATOR: CoordinatorHandler(tool_executor, project_root, quality_gate),
        }

    def get_config(self, agent_name: str) -> Any:
        from agent_runtime.enums import AgentName as AN

        key = AN(agent_name) if isinstance(agent_name, str) else agent_name
        return self._cfg.agents[key]

    def get_handler(self, agent_name: str) -> Any:
        from agent_runtime.enums import AgentName as AN

        key = AN(agent_name) if isinstance(agent_name, str) else agent_name
        return self._handlers[key]

    def bind_runtime(self, runtime: Any) -> None:
        for handler in self._handlers.values():
            bind = getattr(handler, "bind_runtime", None)
            if callable(bind):
                bind(runtime)
