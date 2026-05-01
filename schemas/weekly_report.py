from datetime import datetime
from pydantic import BaseModel


class WeeklyReport(BaseModel):
    period: str
    project_id: str
    project_summary: str
    weekly_progress: str
    risk_summary: str
    blockers: str | None = None
    next_week_plan: str | None = None
    decision_suggestions: str | None = None
    pending_decisions: str | None = None
    document_link: str | None = None
    send_status: str = '未发送'
    generated_at: datetime | None = None
    created_by_agent: str | None = None
