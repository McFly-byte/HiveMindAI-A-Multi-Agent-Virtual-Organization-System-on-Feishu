from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from llm.client import JsonLLMClient, LLMError

FRONTDESK_DIALOGUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "dialogue_action",
        "reply_text",
        "selected_agent_id",
        "intent",
        "mode",
        "confidence",
        "slots",
        "missing_slots",
        "tool_goal",
        "reason",
    ],
    "properties": {
        "dialogue_action": {"type": "string"},
        "reply_text": {"type": "string"},
        "selected_agent_id": {"type": "string"},
        "intent": {"type": "string"},
        "mode": {"type": "string"},
        "confidence": {"type": "number"},
        "slots": {"type": "object"},
        "missing_slots": {"type": "array"},
        "tool_goal": {"type": "string"},
        "reason": {"type": "string"},
    },
}

# Backward-compatible name for imports in existing code.
FRONTDESK_INTENT_SCHEMA = FRONTDESK_DIALOGUE_SCHEMA


@dataclass(frozen=True)
class FrontdeskIntentSuggestion:
    dialogue_action: str
    reply_text: str
    selected_agent_id: str
    intent: str
    mode: str
    confidence: float
    slots: dict[str, Any]
    missing_slots: list[str]
    tool_goal: str
    reason: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrontdeskIntentSuggestion":
        return cls(
            dialogue_action=str(data.get("dialogue_action") or "").strip(),
            reply_text=str(data.get("reply_text") or ""),
            selected_agent_id=str(data.get("selected_agent_id") or "").strip(),
            intent=str(data.get("intent") or "").strip(),
            mode=str(data.get("mode") or "answer").strip(),
            confidence=float(data.get("confidence") or 0.0),
            slots=data.get("slots") if isinstance(data.get("slots"), dict) else {},
            missing_slots=[str(x) for x in data.get("missing_slots", [])] if isinstance(data.get("missing_slots"), list) else [],
            tool_goal=str(data.get("tool_goal") or ""),
            reason=str(data.get("reason") or "").strip(),
        )

    @property
    def need_user_input(self) -> list[str]:
        return self.missing_slots

    def to_payload(self) -> dict[str, Any]:
        return {
            "dialogue_action": self.dialogue_action,
            "reply_text": self.reply_text,
            "selected_agent_id": self.selected_agent_id,
            "intent": self.intent,
            "mode": self.mode,
            "confidence": self.confidence,
            "slots": self.slots,
            "missing_slots": self.missing_slots,
            "tool_goal": self.tool_goal,
            "reason": self.reason,
        }


class FrontdeskIntentParser:
    def __init__(self, llm_client: JsonLLMClient) -> None:
        self.llm_client = llm_client

    def parse(self, *, user_text: str, agent_summaries: list[dict[str, Any]]) -> FrontdeskIntentSuggestion:
        system = (
            "你是事件驱动多 Agent 系统的 FrontDeskAgent，也就是唯一直接和用户对话的聊天机器人前台。"
            "必须只输出一个 JSON object，禁止 Markdown，禁止解释。"
            "JSON 必须包含这些字段：dialogue_action, reply_text, selected_agent_id, intent, mode, confidence, slots, missing_slots, tool_goal, reason。"
            "dialogue_action 只能是：chat_reply, ask_clarification, route_to_agent, list_todos, execute_todo, tool_request。"
            "普通寒暄、无明确任务、说明你能做什么，使用 chat_reply；此时 reply_text 必须是给用户看的自然语言回复，selected_agent_id/intent/tool_goal 用空字符串。"
            "缺信息但还不能派单，使用 ask_clarification；reply_text 是追问。"
            "查看待办使用 list_todos；执行待办使用 execute_todo。"
            "风险/周报/计划等业务任务使用 route_to_agent；selected_agent_id 必须从 candidate_agents 的 id 中选择，且必须给出 intent。"
            "飞书发消息、查人、建群、文档、多维表格等应用工具任务使用 tool_request；selected_agent_id 通常为 feishu_ops_agent，tool_goal 写清楚目标。"
            "mode 可选 answer/create_todo/tool。slots 常用字段：project_name, time_range, recipient, text, table, fields。"
            "没有值的字符串字段填空字符串，不要省略字段。"
            "示例：{\"dialogue_action\":\"chat_reply\",\"reply_text\":\"你好，我是你的项目助理，可以帮你整理风险、创建待办、生成周报，也可以调用飞书应用工具。\",\"selected_agent_id\":\"\",\"intent\":\"\",\"mode\":\"answer\",\"confidence\":1,\"slots\":{},\"missing_slots\":[],\"tool_goal\":\"\",\"reason\":\"用户只是寒暄\"}。"
        )
        user = json.dumps({"user_text": user_text, "candidate_agents": agent_summaries}, ensure_ascii=False)
        data = self.llm_client.complete_json(system=system, user=user, schema=FRONTDESK_DIALOGUE_SCHEMA)
        suggestion = FrontdeskIntentSuggestion.from_dict(data)
        valid_actions = {"chat_reply", "ask_clarification", "route_to_agent", "list_todos", "execute_todo", "tool_request"}
        if suggestion.dialogue_action not in valid_actions:
            raise LLMError(f"FrontDesk LLM 返回未知 dialogue_action: {suggestion.dialogue_action}")
        if suggestion.dialogue_action in {"route_to_agent", "tool_request"} and (not suggestion.selected_agent_id or not suggestion.intent):
            raise LLMError("FrontDesk LLM 在 route/tool 动作中没有给出 selected_agent_id/intent")
        if suggestion.dialogue_action in {"chat_reply", "ask_clarification"} and not suggestion.reply_text:
            raise LLMError("FrontDesk LLM 在聊天/追问动作中没有给出 reply_text")
        return suggestion
