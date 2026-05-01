from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class AgentRunRecord(BaseModel):
    run_id: str
    agent_name: str
    trigger_type: Literal['手动', '定时', '数据变更', '周报周期'] | str
    input_tables: list[str]
    input_record_ids: list[str]
    output_tables: list[str]
    output_record_ids: list[str]
    action: str
    reasoning_summary: str
    status: Literal['成功', '失败', '部分成功']
    error_message: str | None = None
    start_time: datetime
    end_time: datetime | None = None
