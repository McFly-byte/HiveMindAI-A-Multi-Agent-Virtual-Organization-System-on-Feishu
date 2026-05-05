from __future__ import annotations

import json
from typing import Any, Protocol
from uuid import uuid4

from agent_runtime.agent_io import (
    AbnormalSignal,
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
from agent_runtime.base_refs import OutputRecordRef
from agent_runtime.enums import (
    ActionType,
    AgentName,
    BaseTableName,
    EvidenceSourceType,
    FollowUpStatus,
    MilestoneStatus,
    Priority,
    ProjectHealth,
    ProjectStatus,
    ReportSendStatus,
    RiskLevel,
    RiskType,
    TaskStatus,
)
from agent_runtime.errors import AgentRuntimeError
from agent_runtime.interfaces import QualityGateProtocol, ToolExecutorProtocol
from agent_runtime.loaders import load_project_manifest
from agent_runtime.mvp.bitable_fields import record_field_text
from agent_runtime.mvp.project_env import expand_env_value
from agent_runtime.project_state import ProjectManifest
from agent_runtime.quality_gate import QualityGateRequest
from agent_runtime.session import AgentSession


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


class ProjectSecretaryHandler(BaseMvpHandler):
    async def run(self, session: AgentSession, input_payload: Any) -> ProjectStateOutput:
        merged: dict[str, Any] = {"run_id": session.run_id, "project_id": session.project_id}
        if isinstance(input_payload, dict):
            merged.update(input_payload)
        elif input_payload is not None:
            raise AgentRuntimeError("project_secretary expects dict or None input_payload")
        _ = ProjectStateInput.model_validate(merged)
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
                    priority=Priority.P1,
                    blocking_reason=record_field_text(f, "阻塞说明"),
                )
            )

        ms_out: list[MilestoneSnapshot] = []
        for row in mr.get("items") or []:
            rid = str(row.get("record_id") or "")
            f = row.get("fields") or {}
            ms_out.append(
                MilestoneSnapshot(
                    milestone_record_id=rid,
                    milestone_name=record_field_text(f, "里程碑名称") or rid,
                    owner=record_field_text(f, "负责人"),
                    status=_parse_milestone_status(record_field_text(f, "状态")),
                )
            )

        missing: list[MissingFieldFinding] = []
        abnormal: list[AbnormalSignal] = []
        for t in tasks_out:
            if t.status in (TaskStatus.BLOCKED,):
                abnormal.append(
                    AbnormalSignal(
                        signal_type="task_blocked",
                        table_name=BaseTableName.TASKS,
                        record_id=t.task_record_id,
                        severity="high",
                        description=f"任务阻塞: {t.task_name}",
                        evidence_refs=[
                            EvidenceRef(
                                evidence_id=f"ev_{uuid4().hex[:10]}",
                                source_type=EvidenceSourceType.BASE_RECORD,
                                summary=t.task_name,
                                table_name=BaseTableName.TASKS,
                                record_id=t.task_record_id,
                            )
                        ],
                    )
                )
            if not t.owner:
                missing.append(
                    MissingFieldFinding(
                        table_name=BaseTableName.TASKS,
                        record_id=t.task_record_id,
                        field_name="负责人",
                        reason="负责人缺失",
                    )
                )

        summary = f"读取项目「{proj_snap.project_name}」：{len(tasks_out)} 条任务，{len(ms_out)} 条里程碑"
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

        _ = _unwrap_tool_result(
            await self._tools.call_tool(
                "feishu_bitable_search_records",
                {"app_token": app_token, "table_id": risks_table.table_id, "page_size": 30},
                session,
            )
        )

        candidates: list[RiskCandidate] = []
        for sig in data.project_state.abnormal_signals:
            candidates.append(
                RiskCandidate(
                    risk_title=f"信号: {sig.signal_type}",
                    risk_type=RiskType.DELAY,
                    risk_level=RiskLevel.MEDIUM,
                    confidence=0.6,
                    trigger_reason=sig.description,
                    suggested_actions=["安排责任人确认根因"],
                    evidence_refs=list(sig.evidence_refs),
                    idempotency_key=f"risk_{sig.record_id}_{sig.signal_type}",
                )
            )

        summary = f"生成 {len(candidates)} 条风险候选（基于 {len(data.project_state.abnormal_signals)} 个异常信号）"
        return RiskAnalysisOutput(
            run_id=session.run_id,
            project_id=session.project_id,
            agent_name=AgentName.RISK_ANALYSIS,
            summary=summary,
            risk_candidates=candidates,
            evidence_status="sufficient" if candidates else "insufficient",
        )


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

        _ = _unwrap_tool_result(
            await self._tools.call_tool(
                "feishu_bitable_search_records",
                {"app_token": app_token, "table_id": fu_table.table_id, "page_size": 20},
                session,
            )
        )

        requests: list[FollowUpRequest] = []
        for mf in data.missing_fields:
            requests.append(
                FollowUpRequest(
                    followup_title=f"追问: {mf.field_name}",
                    project_id=session.project_id,
                    related_task_id=mf.record_id if mf.table_name == BaseTableName.TASKS else None,
                    followup_reason=mf.reason,
                    questions=[mf.suggested_question or f"请补充字段「{mf.field_name}」"],
                    message=f"项目 {session.project_id} 需要补充 {mf.field_name}",
                    status=FollowUpStatus.TO_SEND,
                    idempotency_key=f"fu_{mf.record_id}_{mf.field_name}",
                    evidence_refs=[],
                )
            )

        summary = f"产出 {len(requests)} 条追问建议（只生成 proposed，不写 Base）"
        return FollowUpOutput(
            run_id=session.run_id,
            project_id=session.project_id,
            agent_name=AgentName.FOLLOWUP,
            summary=summary,
            followup_requests=requests,
        )


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

        _ = _unwrap_tool_result(
            await self._tools.call_tool(
                "feishu_bitable_search_records",
                {"app_token": app_token, "table_id": wr_table.table_id, "page_size": 5},
                session,
            )
        )

        report = WeeklyReportDraft(
            period=data.period,
            project_id=session.project_id,
            project_summary=data.project_state.project.project_name,
            progress=WeeklyReportSection(title="本周进展", items=[data.project_state.summary]),
            risk_summary=WeeklyReportSection(
                title="风险",
                items=[c.risk_title for c in data.risks],
            ),
            blockers=WeeklyReportSection(title="阻塞项", items=[]),
            next_plan=WeeklyReportSection(title="下周计划", items=["继续推进关键路径"]),
            decision_items=WeeklyReportSection(title="待决策", items=[]),
            send_status=ReportSendStatus.DRAFT,
        )

        summary = f"已生成周报草稿（周期 {data.period}）"
        return WeeklyReportOutput(
            run_id=session.run_id,
            project_id=session.project_id,
            agent_name=AgentName.WEEKLY_REPORT,
            summary=summary,
            report=report,
        )


class CoordinatorHandler(BaseMvpHandler):
    def __init__(
        self,
        tool_executor: ToolExecutorProtocol,
        project_root: Any,
        quality_gate: QualityGateProtocol,
    ) -> None:
        super().__init__(tool_executor, project_root)
        self._quality_gate = quality_gate

    async def run(self, session: AgentSession, input_payload: Any) -> CoordinatorPlan:
        await self._trace(session, "coordinator_start")

        payload: dict[str, Any]
        if isinstance(input_payload, dict):
            payload = input_payload
        elif isinstance(input_payload, str) and input_payload.strip():
            payload = json.loads(input_payload)
        else:
            payload = {}

        manifest = self._expanded_manifest()
        app_token = manifest.base_app_token

        proposed = payload.get("proposed_creates") or []

        creates = [RecordCreate.model_validate(x) for x in proposed] if proposed else []

        if creates:
            qreq = QualityGateRequest(
                run_id=session.run_id,
                project_id=session.project_id,
                action_type=ActionType.CREATE_RECORD,
                payload=payload,
                proposed_creates=creates,
                schema_name="CoordinatorWriteBundle",
            )
            qres = await self._quality_gate.verify(qreq)
            if not qres.passed:
                raise AgentRuntimeError(qres.blocked_reason or "quality gate blocked")

            for pc in creates:
                table_id = manifest.tables[pc.table_name].table_id
                cr_out = await self._tools.call_tool(
                    "feishu_bitable_create_record",
                    {"app_token": app_token, "table_id": table_id, "fields": pc.fields},
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

        summary = "协调完成" + (f"，已写回 {len(creates)} 条记录" if creates else "（无写回）")
        return CoordinatorPlan(
            run_id=session.run_id,
            project_id=session.project_id,
            decision="mvp_coordinator_chain",
            called_agents=[],
            evidence_status="sufficient",
            actions=[],
            stop_reason=summary,
        )


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
