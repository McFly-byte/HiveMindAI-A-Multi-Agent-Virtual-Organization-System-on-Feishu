from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field


class Task(BaseModel):
    task_name: str
    project_id: str | None = None
    milestone_id: str | None = None
    owner: str | None = None
    role_type: Literal['产品', '前端', '后端', '算法', '测试', '项目经理'] | None = None
    status: Literal['未开始', '进行中', '阻塞', '待验收', '已完成'] | None = None
    priority: Literal['P0', 'P1', 'P2'] | None = None
    planned_start_time: date | None = None
    due_time: date | None = None
    last_updated_at: datetime | None = None
    estimated_hours: float | None = None
    actual_hours: float | None = None
    dependency_tasks: list[str] = Field(default_factory=list)
    blocker_description: str | None = None
    needs_followup: bool = False
    risk_flag: str | None = None
    agent_summary: str | None = None
