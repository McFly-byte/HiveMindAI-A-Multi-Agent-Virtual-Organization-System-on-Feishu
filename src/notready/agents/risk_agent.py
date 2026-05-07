from __future__ import annotations

from typing import Any

from agents.base import BusinessAgent, contains_any, missing_required, wants_todo
from llm.client import JsonLLMClient
from runtime.models import BusinessRequest, BusinessResponse, CapabilityBid, CapabilityProbe

RISK_RESULT_SCHEMA: dict[str, Any] = {"type": "object", "required": ["title", "summary", "risks", "recommendations"], "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "risks": {"type": "array"}, "recommendations": {"type": "array"}}}


class RiskAgent(BusinessAgent):
    id = "risk_agent"
    name = "风险 Agent"
    tag = "业务 Agent"
    lead = "识别项目风险、缺失字段和风险整理待办。"
    icon = "险"
    accent = "#dc2626"
    domains = ("risk", "blocker", "delay")

    def __init__(self, *, llm_client: JsonLLMClient) -> None:
        self.llm_client = llm_client

    def can_handle(self, probe: CapabilityProbe) -> CapabilityBid:
        text = probe.user_text
        keywords = self.configured_keywords() or ("风险", "延期", "阻塞", "卡点", "问题", "依赖")
        hit = contains_any(text, keywords)
        return CapabilityBid(agent_id=self.id, can_handle=hit, confidence=0.95 if hit else 0.05, matched_intent=self.configured_intent(create_todo=wants_todo(text), answer_intent="risk.report.generate", todo_intent="risk.report.create_todo"), reason="风险分析任务" if hit else "未命中风险域")

    def handle(self, request: BusinessRequest) -> BusinessResponse:
        missing = missing_required(request.known_slots, ("project_name", "time_range"))
        if missing:
            question = "请问要整理哪个项目的风险？" if "project_name" in missing else "请问时间范围是本周、下周还是本月？"
            return BusinessResponse(message_type="need_user_input", agent_id=self.id, missing_slots=missing, suggested_question=question)
        project = str(request.known_slots["project_name"])
        time_range = str(request.known_slots["time_range"])
        if request.mode == "create_todo":
            title = f"整理{project}{_time_label(time_range)}风险"
            return BusinessResponse(message_type="todo_proposal", agent_id=self.id, todo={"title": title, "assigned_agent": self.id, "action_type": "risk.report.generate", "action_args": {"project_name": project, "time_range": time_range}, "task_context": {"summary": f"用户希望整理{project}{_time_label(time_range)}风险，并加入待办。", "slots": {"project_name": project, "time_range": time_range}, "constraints": request.constraints}, "user_visible_summary": title})
        return BusinessResponse(message_type="business_result", agent_id=self.id, result=self._generate_with_llm(project=project, time_range=time_range, request=request))

    def _generate_with_llm(self, *, project: str, time_range: str, request: BusinessRequest) -> dict[str, Any]:
        system = "你是项目 PMO 风险分析 Agent。只输出 JSON object。字段必须包含 title, summary, risks, recommendations。risks 是数组，每项包含 name, severity, reason, mitigation。"
        user = f"项目：{project}\n时间范围：{_time_label(time_range)}\n用户目标：{request.user_goal}\n任务上下文：{request.task_context or {}}\n请生成适合项目管理汇报的风险摘要，风险数量 3-5 个。"
        obj = self.llm_client.complete_json(system=system, user=user, schema=RISK_RESULT_SCHEMA)
        risks = obj.get("risks") if isinstance(obj.get("risks"), list) else []
        recommendations = obj.get("recommendations") if isinstance(obj.get("recommendations"), list) else []
        risk_lines = []
        for i, risk in enumerate(risks, 1):
            if isinstance(risk, dict):
                risk_lines.append(f"{i}. [{risk.get('severity', '未评级')}] {risk.get('name', f'风险 {i}')}：{risk.get('reason', '')}；建议：{risk.get('mitigation', '')}")
            else:
                risk_lines.append(f"{i}. {risk}")
        rec_lines = [f"{i}. {item}" for i, item in enumerate(recommendations, 1)]
        summary = str(obj.get("summary") or "").strip()
        if risk_lines:
            summary += "\n\n主要风险：\n" + "\n".join(risk_lines)
        if rec_lines:
            summary += "\n\n建议动作：\n" + "\n".join(rec_lines)
        return {"title": str(obj.get("title") or f"{project}{_time_label(time_range)}风险摘要"), "summary": summary.strip(), "data": {"risk_count": len(risks), "project_name": project, "time_range": time_range, "raw": obj}}


def _time_label(value: str) -> str:
    return {"this_week": "本周", "next_week": "下周", "today": "今日", "tomorrow": "明日", "this_month": "本月"}.get(value, str(value))
