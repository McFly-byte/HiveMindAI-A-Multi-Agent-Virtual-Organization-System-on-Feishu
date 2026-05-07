from __future__ import annotations

from agents.base import BusinessAgent, contains_any, missing_required, wants_todo
from runtime.models import BusinessRequest, BusinessResponse, CapabilityBid, CapabilityProbe


class ProjectPlanAgent(BusinessAgent):
    id = "project_plan_agent"
    name = "计划 Agent"
    tag = "业务 Agent"
    lead = "拆解目标、里程碑、任务和负责人；生成计划建议或计划类待办。"
    icon = "计"
    accent = "#7c3aed"
    domains = ("plan", "milestone", "task")

    def can_handle(self, probe: CapabilityProbe) -> CapabilityBid:
        text = probe.user_text
        keywords = self.configured_keywords() or ("计划", "里程碑", "排期", "任务拆解", "拆解", "路线图")
        hit = contains_any(text, keywords)
        conf = 0.88 if hit else 0.1
        return CapabilityBid(
            agent_id=self.id,
            can_handle=hit,
            confidence=conf,
            matched_intent=self.configured_intent(create_todo=wants_todo(text), answer_intent="project_plan.generate", todo_intent="project_plan.create_todo"),
            reason="命中计划/里程碑/排期类关键词" if hit else "未命中计划域关键词",
        )

    def handle(self, request: BusinessRequest) -> BusinessResponse:
        missing = missing_required(request.known_slots, ("project_name",))
        if missing:
            return BusinessResponse(
                message_type="need_user_input",
                agent_id=self.id,
                missing_slots=missing,
                suggested_question="请问要拆解哪个项目或目标？",
            )
        project = request.known_slots["project_name"]
        if request.mode == "create_todo":
            title = f"拆解{project}后续计划"
            return BusinessResponse(
                message_type="todo_proposal",
                agent_id=self.id,
                todo={
                    "title": title,
                    "assigned_agent": self.id,
                    "action_type": "project_plan.generate",
                    "action_args": {"project_name": project},
                    "task_context": {
                        "summary": f"用户希望拆解{project}后续计划。",
                        "slots": {"project_name": project},
                        "constraints": request.constraints,
                    },
                    "user_visible_summary": title,
                },
            )
        return BusinessResponse(
            message_type="business_result",
            agent_id=self.id,
            result={
                "title": f"{project}后续计划建议",
                "summary": "计划草稿：先明确目标和责任人，再拆分里程碑、交付物、风险和跟进节奏，最后进入执行与复盘。",
                "data": {"milestones": ["Runtime MVP", "Feishu Tools", "Persistence"]},
            },
        )
