from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from agent_runtime.base_refs import BaseRecordRef, EvidenceRef, RecordCreate, RecordPatch
from agent_runtime.enums import (
    ActionType,
    AgentName,
    BaseTableName,
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


class AgentOutputBase(BaseModel):
    """Common structured output contract for all business agents."""

    run_id: str
    project_id: str
    agent_name: AgentName
    summary: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    proposed_creates: list[RecordCreate] = Field(default_factory=list)
    proposed_patches: list[RecordPatch] = Field(default_factory=list)
    need_human_confirm: bool = False
    blocked: bool = False
    blocked_reason: str | None = None


class CoordinatorAction(BaseModel):
    """One action selected by the coordinator."""

    action_type: ActionType
    target_agent: AgentName | None = None
    target_table: BaseTableName | None = None
    payload_ref: str | None = None
    reason: str
    require_quality_gate: bool = True
    require_human_confirm: bool = False


class CoordinatorPlan(BaseModel):
    """Coordinator decision and action plan."""

    run_id: str
    project_id: str
    decision: str
    called_agents: list[AgentName] = Field(default_factory=list)
    evidence_status: Literal["unknown", "insufficient", "sufficient"] = "unknown"
    actions: list[CoordinatorAction] = Field(default_factory=list)
    stop_reason: str | None = None


class ProjectRecordSnapshot(BaseModel):
    """Snapshot of the project record read from Base."""

    project_record_id: str
    project_name: str
    owner: str | None = None
    status: ProjectStatus
    priority: Priority
    health: ProjectHealth | None = None
    risk_level: RiskLevel | None = None
    start_date: date | None = None
    target_release_date: date | None = None
    weekly_progress: str | None = None


class TaskSnapshot(BaseModel):
    """Snapshot of one task record read from Base."""

    task_record_id: str
    task_name: str
    owner: str | None = None
    role_type: str | None = None
    status: TaskStatus
    priority: Priority
    due_date: date | None = None
    last_updated_at: date | None = None
    estimated_hours: float | None = None
    actual_hours: float | None = None
    dependency_task_ids: list[str] = Field(default_factory=list)
    blocking_reason: str | None = None
    need_followup: bool = False
    risk_mark: str | None = None


class MilestoneSnapshot(BaseModel):
    """Snapshot of one milestone record read from Base."""

    milestone_record_id: str
    milestone_name: str
    owner: str | None = None
    planned_date: date | None = None
    actual_date: date | None = None
    status: MilestoneStatus
    delay_days: int | None = None
    dependency_summary: str | None = None
    risk_mark: str | None = None


class MissingFieldFinding(BaseModel):
    """A missing field that should be resolved by follow-up."""

    table_name: BaseTableName
    record_id: str
    field_name: str
    owner: str | None = None
    reason: str
    suggested_question: str | None = None


class AbnormalSignal(BaseModel):
    """An abnormal project/task/milestone signal found during read-only scan."""

    signal_type: str
    table_name: BaseTableName
    record_id: str
    severity: Literal["low", "medium", "high"]
    description: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ProjectStateInput(BaseModel):
    """Input for ProjectSecretaryAgent."""

    run_id: str
    project_id: str
    target_records: list[BaseRecordRef] = Field(default_factory=list)
    time_range_days: int = 14
    include_weekly_reports: bool = True
    include_followups: bool = True
    include_risks: bool = True


class ProjectStateOutput(AgentOutputBase):
    """Structured project state produced by ProjectSecretaryAgent."""

    project: ProjectRecordSnapshot
    tasks: list[TaskSnapshot] = Field(default_factory=list)
    milestones: list[MilestoneSnapshot] = Field(default_factory=list)
    missing_fields: list[MissingFieldFinding] = Field(default_factory=list)
    abnormal_signals: list[AbnormalSignal] = Field(default_factory=list)
    followup_requests: list[str] = Field(default_factory=list)


class RiskCandidate(BaseModel):
    """Risk candidate proposed by RiskAnalysisAgent."""

    risk_title: str
    risk_type: RiskType
    risk_level: RiskLevel
    confidence: float = Field(ge=0.0, le=1.0)
    trigger_reason: str
    responsible_owner: str | None = None
    related_task_ids: list[str] = Field(default_factory=list)
    related_milestone_ids: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    need_followup: bool = False
    followup_target_role: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    idempotency_key: str


class RiskAnalysisInput(BaseModel):
    """Input for RiskAnalysisAgent."""

    run_id: str
    project_id: str
    project_state: ProjectStateOutput
    historical_risk_record_ids: list[str] = Field(default_factory=list)
    followup_record_ids: list[str] = Field(default_factory=list)


class RiskAnalysisOutput(AgentOutputBase):
    """Structured risk analysis output."""

    risk_candidates: list[RiskCandidate] = Field(default_factory=list)
    project_health_recommendation: ProjectHealth | None = None
    project_risk_level_recommendation: RiskLevel | None = None
    evidence_status: Literal["insufficient", "sufficient"] = "insufficient"
    need_more_evidence: bool = False
    requested_evidence: list[str] = Field(default_factory=list)


class FollowUpRequest(BaseModel):
    """Concrete follow-up proposal generated by FollowUpAgent."""

    followup_title: str
    project_id: str
    related_task_id: str | None = None
    related_risk_id: str | None = None
    target_role: str | None = None
    target_user: str | None = None
    followup_reason: str
    questions: list[str]
    message: str
    status: FollowUpStatus = FollowUpStatus.TO_SEND
    idempotency_key: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class FollowUpInput(BaseModel):
    """Input for FollowUpAgent."""

    run_id: str
    project_id: str
    missing_fields: list[MissingFieldFinding] = Field(default_factory=list)
    risk_candidates: list[RiskCandidate] = Field(default_factory=list)
    existing_followup_record_ids: list[str] = Field(default_factory=list)
    recover_replies: bool = False


class FollowUpOutput(AgentOutputBase):
    """Structured follow-up proposals."""

    followup_requests: list[FollowUpRequest] = Field(default_factory=list)
    recovered_reply_summaries: list[str] = Field(default_factory=list)


class WeeklyReportSection(BaseModel):
    """One evidence-backed section in a weekly report draft."""

    title: str
    items: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class WeeklyReportDraft(BaseModel):
    """Management-facing weekly report draft."""

    period: str
    project_id: str
    project_summary: str
    progress: WeeklyReportSection
    risk_summary: WeeklyReportSection
    blockers: WeeklyReportSection
    next_plan: WeeklyReportSection
    decision_items: WeeklyReportSection
    send_status: ReportSendStatus = ReportSendStatus.DRAFT
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class WeeklyReportInput(BaseModel):
    """Input for WeeklyReportAgent."""

    run_id: str
    project_id: str
    period: str
    project_state: ProjectStateOutput
    risks: list[RiskCandidate] = Field(default_factory=list)
    followups: list[FollowUpRequest] = Field(default_factory=list)
    last_week_report_record_id: str | None = None


class WeeklyReportOutput(AgentOutputBase):
    """Structured weekly report output."""

    report: WeeklyReportDraft | None = None
    missing_required_sections: list[str] = Field(default_factory=list)
