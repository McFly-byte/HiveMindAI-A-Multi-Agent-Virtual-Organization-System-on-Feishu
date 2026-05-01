from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class Risk(BaseModel):
    title: str
    project_id: str | None = None
    task_id: str | None = None
    risk_type: Literal['延期风险', '依赖阻塞风险', '资源冲突风险', '需求变更风险', '沟通失真风险', '数据缺失风险']
    risk_level: Literal['低', '中', '高']
    trigger_reason: str
    status: Literal['待确认', '跟进中', '已升级', '已关闭'] = '待确认'
    owner: str | None = None
    suggested_action: str | None = None
    should_escalate: bool = False
    escalation_target: str | None = None
    last_updated_at: datetime | None = None
    created_by_agent: str | None = None
    evidence_source: str | None = None
