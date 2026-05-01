from datetime import date
from typing import Literal
from pydantic import BaseModel


class Project(BaseModel):
    project_name: str
    owner: str | None = None
    status: Literal['未开始', '进行中', '阻塞', '已延期', '已完成'] | None = None
    priority: Literal['P0', 'P1', 'P2'] | None = None
    start_date: date | None = None
    target_launch_date: date | None = None
    health: Literal['健康', '关注', '风险', '严重风险'] | None = None
    risk_level: Literal['低', '中', '高'] | None = None
    weekly_progress: str | None = None
    latest_report_link: str | None = None
