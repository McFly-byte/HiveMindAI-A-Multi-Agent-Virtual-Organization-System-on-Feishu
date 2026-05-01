from datetime import date, datetime, timedelta, timezone
from typing import Any
from agent_runtime.result import ToolResult


class RiskRuleTool:
    """Deterministic MVP risk rule placeholder."""
    tool_name = "RiskRuleTool"

    def evaluate_task(self, task: dict[str, Any]) -> list[str]:
        """Return simple rule hits without writing business state."""
        hits: list[str] = []
        status = task.get("状态") or task.get("status")
        blocker = task.get("阻塞说明") or task.get("blocker_description")
        due = task.get("截止时间") or task.get("due_time")
        updated = task.get("最近更新时间") or task.get("last_updated_at")
        if status == "阻塞" and not blocker:
            hits.append("阻塞任务缺少阻塞说明")
        if isinstance(due, date) and due < date.today() and status != "已完成":
            hits.append("任务已超期")
        if isinstance(updated, datetime) and updated < datetime.now(timezone.utc) - timedelta(days=3):
            hits.append("任务长期未更新")
        return hits

    def run(self, task: dict[str, Any]) -> ToolResult:
        hits = self.evaluate_task(task)
        return ToolResult(tool_name=self.tool_name, success=True, inputs_summary="task rule evaluation", outputs_summary=",".join(hits) if hits else "未命中风险规则")
