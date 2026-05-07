from __future__ import annotations

import hashlib
import time
from typing import Any

from agents.base import BusinessAgent, contains_any
from llm.client import JsonLLMClient
from llm.tool_planner import FeishuToolPlanner
from runtime.models import BusinessRequest, BusinessResponse, CapabilityBid, CapabilityProbe


class FeishuOpsAgent(BusinessAgent):
    id = "feishu_ops_agent"
    name = "飞书操作 Agent"
    tag = "应用工具 Agent"
    lead = "把用户请求交给 LLM 规划，只调用应用身份可用的飞书工具。"
    icon = "飞"
    accent = "#16a34a"
    domains = ("feishu", "tool", "notification")

    def __init__(self, *, llm_client: JsonLLMClient, tool_catalog: list[dict[str, Any]]) -> None:
        self.planner = FeishuToolPlanner(llm_client, tool_catalog)

    def can_handle(self, probe: CapabilityProbe) -> CapabilityBid:
        text = probe.user_text
        keywords = self.configured_keywords() or ("飞书", "发送", "发给", "通知", "私聊", "转达", "多维表格", "表格", "记录")
        hit = contains_any(text, keywords)
        confidence = 1.0 if hit else 0.05
        return CapabilityBid(agent_id=self.id, can_handle=hit, confidence=confidence, matched_intent="feishu.app_tools.plan_and_execute", reason="飞书应用工具任务" if hit else "未命中飞书应用工具域")

    def handle(self, request: BusinessRequest) -> BusinessResponse:
        if request.known_slots.get("operation") == "write_risks_to_bitable":
            return self._build_write_risks_plan(request)
        plan = self.planner.plan(user_text=request.raw_user_text or request.user_goal, known_slots=request.known_slots or {})
        return BusinessResponse(message_type="tool_request", agent_id=self.id, tool={"name": "__tool_plan__", "args": plan, "success_message": plan.get("success_message") or "飞书应用工具计划已完成。"})

    def _build_write_risks_plan(self, request: BusinessRequest) -> BusinessResponse:
        project = str(request.known_slots.get("project_name") or "未命名项目")
        time_range = str(request.known_slots.get("time_range") or "this_week")
        risk_result = request.known_slots.get("risk_result") if isinstance(request.known_slots.get("risk_result"), dict) else {}
        raw = ((risk_result.get("data") or {}).get("raw") if isinstance(risk_result.get("data"), dict) else None) or risk_result
        risks = raw.get("risks") if isinstance(raw.get("risks"), list) else []
        if not risks:
            raise RuntimeError("风险结果中没有可写入 Risks 表的 risks 数组")
        now_ms = int(time.time() * 1000)
        records: list[dict[str, Any]] = []
        for idx, risk in enumerate(risks, 1):
            if not isinstance(risk, dict):
                risk = {"name": str(risk), "severity": "中", "reason": str(risk), "mitigation": "待 PMO 进一步确认。"}
            name = str(risk.get("name") or risk.get("title") or f"{project}风险{idx}").strip()
            reason = str(risk.get("reason") or risk.get("trigger") or risk.get("description") or "由风险分析 Agent 生成，待确认。")
            mitigation = str(risk.get("mitigation") or risk.get("recommendation") or risk.get("建议动作") or "建议负责人确认影响范围并制定缓解计划。")
            severity = _normalize_severity(str(risk.get("severity") or risk.get("level") or "中"))
            risk_type = _normalize_risk_type(str(risk.get("type") or risk.get("category") or name + reason))
            idem = _idempotency_key(project, time_range, name, reason)
            records.append({
                "fields": {
                    "风险标题": name,
                    "所属项目": project,
                    "风险类型": risk_type,
                    "风险等级": severity,
                    "触发原因": reason,
                    "当前状态": "待确认",
                    "建议动作": mitigation,
                    "是否升级": severity == "高",
                    "最近更新时间": now_ms,
                    "由哪个 Agent 创建": "risk_agent",
                    "证据来源": f"RiskAgent LLM 风险摘要 / {time_range}",
                    "幂等键": idem,
                }
            })
        plan = {
            "goal": f"把 {project} 的风险分析结果写入配置好的 Risks 风险问题表",
            "success_message": f"已将 {len(records)} 条风险写入多维表格风险问题表。",
            "steps": [
                {
                    "id": "write_risks_to_bitable",
                    "tool_name": "feishu.bitable.batch_create_named_records",
                    "args": {"table_key": "Risks", "records": records, "strict": True},
                }
            ],
        }
        return BusinessResponse(message_type="tool_request", agent_id=self.id, tool={"name": "__tool_plan__", "args": plan, "success_message": plan["success_message"]})


def _normalize_severity(value: str) -> str:
    text = value.strip().lower()
    if any(x in text for x in ("高", "high", "严重", "p0", "p1")):
        return "高"
    if any(x in text for x in ("低", "low", "轻微")):
        return "低"
    return "中"


def _normalize_risk_type(value: str) -> str:
    text = value.strip()
    candidates = [
        ("延期风险", ("延期", "delay", "进度", "排期")),
        ("依赖阻塞风险", ("依赖", "阻塞", "block", "等待")),
        ("资源冲突风险", ("资源", "人手", "冲突", "占用")),
        ("需求变更风险", ("需求", "变更", "scope", "范围")),
        ("沟通失真风险", ("沟通", "同步", "误解", "信息")),
        ("数据缺失风险", ("数据", "缺失", "未知", "不完整")),
        ("交付质量风险", ("质量", "测试", "缺陷", "bug", "返工")),
    ]
    for label, keys in candidates:
        if any(k in text for k in keys):
            return label
    return "交付质量风险"


def _idempotency_key(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return "risk_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
