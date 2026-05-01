from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class FollowUp(BaseModel):
    title: str
    project_id: str | None = None
    task_id: str | None = None
    risk_id: str | None = None
    assignee: str | None = None
    reason: str
    content: str
    status: Literal['待发送', '待回复', '已回复', '已关闭'] = '待发送'
    reply_content: str | None = None
    asked_at: datetime | None = None
    replied_at: datetime | None = None
    written_back: bool = False
