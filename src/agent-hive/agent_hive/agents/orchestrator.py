from __future__ import annotations

import json
from typing import Any

from agent_hive.agents.base import BaseAgent
from agent_hive.context.manager import AgentContext
from agent_hive.observability.logging import get_logger
from agent_hive.schemas.agent import AgentOutput
from agent_hive.schemas.orchestration import OrchestrationAction
from agent_hive.schemas.tool import ToolIntent


logger = get_logger("agents.orchestrator")


class OrchestratorAgent(BaseAgent):
    """LLM-backed orchestration brain.

    The orchestrator does not execute business tools. It reads the inbound
    event, asks the configured LLM for a routing plan, and returns explicit
    orchestration actions for the runtime to execute.
    """

    async def run(self, context: AgentContext, payload: dict[str, Any]) -> AgentOutput:
        step = context.session.add_step("agent", "orchestrator.run", _truncate_json(payload))
        if context.event.event_type == "feishu.im.message.received":
            logger.info(
                "orchestrator received Feishu message run_id=%s chat_id=%s message_id=%s sender_open_id=%s text=%s",
                context.session.run_id,
                payload.get("chat_id"),
                payload.get("message_id"),
                payload.get("sender_open_id"),
                payload.get("text"),
            )

        raw_decision = await self._decide(context, payload)
        actions = _coerce_actions(raw_decision, source_payload=payload, valid_agent_ids=set(context.event.metadata.get("agent_ids") or []))
        if not actions:
            actions = _fallback_actions(payload, event_type=context.event.event_type)
        intents = _coerce_tool_intents(raw_decision, default_requester=self.config.agent_id)
        summary = str(raw_decision.get("summary") or _summary_for_actions(actions) or f"orchestrator handled {context.event.event_type}")
        output_payload = {
            "decision": raw_decision,
            "actions": [item.model_dump(mode="json") for item in actions],
            "tool_intents": [item.model_dump(mode="json") for item in intents],
        }

        logger.info(
            "orchestrator decision run_id=%s summary=%s action_count=%s tool_intent_count=%s actions=%s",
            context.session.run_id,
            summary,
            len(actions),
            len(intents),
            _truncate_json([item.model_dump(mode="json") for item in actions]),
        )
        step.finish(summary, {"orchestration_action_count": len(actions), "tool_intent_count": len(intents)})
        return AgentOutput(
            agent_id=self.config.agent_id,
            run_id=context.session.run_id,
            summary=summary,
            payload=output_payload,
            orchestration_actions=actions,
            tool_intents=intents,
        )

    async def _decide(self, context: AgentContext, payload: dict[str, Any]) -> dict[str, Any]:
        explicit = payload.get("orchestration_decision")
        if isinstance(explicit, dict):
            logger.debug("orchestrator using explicit orchestration_decision run_id=%s", context.session.run_id)
            return explicit

        if self.llm_provider is not None and self.config.model is not None:
            logger.info(
                "orchestrator calling LLM run_id=%s provider=%s model=%s",
                context.session.run_id,
                self.config.model.provider,
                self.config.model.name,
            )
            return await self.llm_provider.generate_json(
                model_config=self.config.model,
                messages=[
                    {"role": "system", "content": self._system_prompt(context)},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "event": context.event.model_dump(mode="json"),
                                "payload": payload,
                                "available_agents": _available_business_agents(context),
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    },
                ],
            )

        logger.warning("orchestrator has no LLM provider/model; using deterministic fallback")
        return {
            "summary": f"orchestrator fallback for {context.event.event_type}",
            "actions": [item.model_dump(mode="json") for item in _fallback_actions(payload, event_type=context.event.event_type)],
        }

    def _system_prompt(self, context: AgentContext) -> str:
        return (
            f"{context.render(max_chars=12000)}\n\n"
            "You are the HiveMindAI orchestrator. Decide which business agents should run for this event.\n"
            "Return one JSON object only with this schema:\n"
            "{\n"
            '  "thought": "short routing rationale",\n'
            '  "summary": "short operator-readable summary",\n'
            '  "actions": [\n'
            '    {"action_type":"run_agent","target_agent_id":"project_secretary|data_gap_inspector|risk_analysis|followup|weekly_report",'
            '"payload":{"summary":"why this agent should run","use_llm":true},"reason":"why"}\n'
            "  ],\n"
            '  "tool_intents": []\n'
            "}\n"
            "Rules:\n"
            "- Prefer running a business agent instead of doing the work yourself.\n"
            "- Set payload.use_llm=true for business agent actions unless explicitly unnecessary.\n"
            "- Do not call Feishu tools directly. If a direct Feishu intent is absolutely necessary, emit a tool_intent.\n"
            "- For ordinary Feishu chat messages, route to project_secretary unless the request clearly matches another agent.\n"
            "- For event_type=fr02.inspection.requested, route to data_gap_inspector and preserve the source payload.\n"
            "- Use risk_analysis for risk/issue/blocker requests, followup for missing info/follow-up tracking, weekly_report for reports.\n"
        )


def _available_business_agents(context: AgentContext) -> list[dict[str, str]]:
    agents: list[dict[str, str]] = []
    for agent_id, config in context.agent_config.metadata.get("available_agents", {}).items():
        if isinstance(config, dict):
            agents.append({"agent_id": agent_id, "description": str(config.get("description") or "")})
    if agents:
        return agents
    return [
        {"agent_id": "project_secretary", "description": "default PMO/project secretary agent"},
        {"agent_id": "data_gap_inspector", "description": "FR-02 scheduled task, milestone and meeting-minutes data gap inspection"},
        {"agent_id": "risk_analysis", "description": "risk, issue, blocker, abnormal signal analysis"},
        {"agent_id": "followup", "description": "follow-up questions and missing information tracking"},
        {"agent_id": "weekly_report", "description": "weekly status/report generation"},
    ]


def _coerce_actions(
    raw: dict[str, Any],
    *,
    source_payload: dict[str, Any],
    valid_agent_ids: set[str] | None = None,
) -> list[OrchestrationAction]:
    items = raw.get("actions") or raw.get("orchestration_actions") or []
    if not isinstance(items, list):
        return []
    actions: list[OrchestrationAction] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        data = dict(item)
        data.setdefault("action_type", "run_agent")
        data.setdefault("target_agent_id", data.get("agent_id") or data.get("target"))
        target = data.get("target_agent_id")
        if valid_agent_ids and target not in valid_agent_ids:
            logger.warning("dropping orchestration action for unknown agent target_agent_id=%s", target)
            continue
        if data["action_type"] != "run_agent":
            logger.warning("dropping unsupported orchestration action action_type=%s", data["action_type"])
            continue
        payload = data.get("payload")
        data["payload"] = {**source_payload, **(payload if isinstance(payload, dict) else {})}
        data["payload"].setdefault("use_llm", True)
        actions.append(OrchestrationAction.model_validate(data))
    return actions


def _coerce_tool_intents(raw: dict[str, Any], *, default_requester: str) -> list[ToolIntent]:
    items = raw.get("tool_intents") or raw.get("feishu_intents") or raw.get("tool_calls") or []
    if isinstance(raw.get("feishu_intent"), dict):
        items = [raw["feishu_intent"], *items]
    if not isinstance(items, list):
        return []
    intents: list[ToolIntent] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        data = _extract_tool_intent_payload(item)
        if data is None:
            logger.warning("dropping malformed orchestrator tool intent item=%s", _truncate_json(item, limit=500))
            continue
        data.setdefault("requested_by_agent_id", default_requester)
        try:
            intents.append(ToolIntent.model_validate(data))
        except Exception as exc:
            logger.warning("dropping invalid orchestrator tool intent error=%s item=%s", exc, _truncate_json(item, limit=500))
    return intents


def _extract_tool_intent_payload(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("call_type") == "feishu_intent":
        intent = item.get("intent") or item.get("tool_intent") or item.get("feishu_intent")
        if isinstance(intent, dict):
            return dict(intent)
        return None
    if isinstance(item.get("intent"), dict) and not item.get("domain"):
        return dict(item["intent"])
    if item.get("domain") and item.get("action"):
        return dict(item)
    return None


def _fallback_actions(payload: dict[str, Any], *, event_type: str) -> list[OrchestrationAction]:
    if payload.get("target_agent_id"):
        target = str(payload["target_agent_id"])
    elif event_type == "fr02.inspection.requested":
        target = "data_gap_inspector"
    elif event_type == "feishu.im.message.received":
        target = _classify_feishu_text(str(payload.get("text") or ""))
    else:
        target = "project_secretary"
    return [
        OrchestrationAction(
            action_type="run_agent",
            target_agent_id=target,
            payload={**payload, "summary": payload.get("summary") or f"orchestrated event {event_type}", "use_llm": True},
            reason="deterministic fallback route",
        )
    ]


def _classify_feishu_text(text: str) -> str:
    lowered = text.lower()
    if any(word in text for word in ("风险", "阻塞", "问题", "异常")) or any(word in lowered for word in ("risk", "blocker", "issue")):
        return "risk_analysis"
    if any(word in text for word in ("周报", "日报", "报告", "汇总")) or "report" in lowered:
        return "weekly_report"
    if any(word in text for word in ("跟进", "追问", "补充", "缺少")) or "follow" in lowered:
        return "followup"
    return "project_secretary"


def _summary_for_actions(actions: list[OrchestrationAction]) -> str:
    targets = [item.target_agent_id for item in actions if item.target_agent_id]
    if not targets:
        return ""
    return f"orchestrator routed to {', '.join(targets)}"


def _truncate_json(value: Any, *, limit: int = 1200) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
