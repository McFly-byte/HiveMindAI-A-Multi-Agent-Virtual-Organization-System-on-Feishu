from datetime import date
from typing import Literal
from pydantic import BaseModel


class Milestone(BaseModel):
    milestone_name: str
    project_id: str | None = None
    owner: str | None = None
    planned_date: date | None = None
    actual_date: date | None = None
    status: Literal['未开始', '进行中', '已完成', '已延期'] | None = None
    delay_days: int | None = None
    dependency_note: str | None = None
    summary: str | None = None
    risk_flag: str | None = None
