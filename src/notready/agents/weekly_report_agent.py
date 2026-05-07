from __future__ import annotations

from agents.base import BusinessAgent, contains_any, missing_required, wants_todo
from runtime.models import BusinessRequest, BusinessResponse, CapabilityBid, CapabilityProbe


class WeeklyReportAgent(BusinessAgent):
    id = "weekly_report_agent"
    name = "周报 Agent"
    tag = "业务 Agent"
    lead = "汇总项目进展、风险、下周计划，生成周报草稿或周报待办。"
    icon = "报"
    accent = "#2563eb"
    domains = ("weekly_report", "summary")

    def can_handle(self, probe: CapabilityProbe) -> CapabilityBid:
        text = probe.user_text
        keywords = self.configured_keywords() or ("周报", "汇报", "总结", "进展", "本周完成")
        hit = contains_any(text, keywords)
        conf = 0.91 if "周报" in text else 0.65 if hit else 0.1
        return CapabilityBid(
            agent_id=self.id,
            can_handle=hit,
            confidence=conf,
            matched_intent=self.configured_intent(create_todo=wants_todo(text), answer_intent="weekly_report.generate", todo_intent="weekly_report.create_todo"),
            reason="命中周报/总结类关键词" if hit else "未命中周报域关键词",
        )

    def handle(self, request: BusinessRequest) -> BusinessResponse:
        missing = missing_required(request.known_slots, ("project_name", "time_range"))
        if missing:
            return BusinessResponse(
                message_type="need_user_input",
                agent_id=self.id,
                missing_slots=missing,
                suggested_question="请问要生成哪个项目、哪个时间范围的周报？",
            )
        project = request.known_slots["project_name"]
        time_range = request.known_slots["time_range"]
        if request.mode == "create_todo":
            title = f"生成{project}{_time_label(time_range)}周报草稿"
            return BusinessResponse(
                message_type="todo_proposal",
                agent_id=self.id,
                todo={
                    "title": title,
                    "assigned_agent": self.id,
                    "action_type": "weekly_report.generate",
                    "action_args": {"project_name": project, "time_range": time_range},
                    "task_context": {
                        "summary": f"用户希望生成{project}{_time_label(time_range)}周报草稿。",
                        "slots": {"project_name": project, "time_range": time_range},
                        "constraints": request.constraints,
                    },
                    "user_visible_summary": title,
                },
            )
        return BusinessResponse(
            message_type="business_result",
            agent_id=self.id,
            result={
                "title": f"{project}{_time_label(time_range)}周报草稿",
                "summary": "周报草稿：本周完成关键事项梳理、风险清单整理和待办跟进；下周建议推进数据回写、责任人确认和风险闭环。",
                "data": {"sections": ["本周进展", "主要风险", "下周计划"]},
            },
        )


def _time_label(value: str) -> str:
    return {
        "this_week": "本周",
        "next_week": "下周",
        "this_month": "本月",
        "today": "今日",
    }.get(value, str(value))
