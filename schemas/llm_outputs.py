from pydantic import BaseModel, Field


class RiskAnalysisLLMOutput(BaseModel):
    trigger_reason: str
    suggested_action: str
    evidence_summary: str
    confidence: float = Field(ge=0, le=1)


class FollowUpLLMOutput(BaseModel):
    title: str
    content: str
    reason: str


class WeeklyReportLLMOutput(BaseModel):
    project_summary: str
    weekly_progress: str
    risk_summary: str
    blockers: str
    next_week_plan: str
    pending_decisions: str
