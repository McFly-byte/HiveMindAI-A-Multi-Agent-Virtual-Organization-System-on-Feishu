from __future__ import annotations

import json
from typing import Any

from llm.client import JsonLLMClient, LLMError

FEISHU_TOOL_PLAN_SCHEMA: dict[str, Any] = {"type": "object", "required": ["goal", "success_message", "steps"], "properties": {"goal": {"type": "string"}, "success_message": {"type": "string"}, "steps": {"type": "array"}}}


class FeishuToolPlanner:
    def __init__(self, llm_client: JsonLLMClient, tool_catalog: list[dict[str, Any]]) -> None:
        self.llm_client = llm_client
        self.tool_catalog = [tool for tool in tool_catalog if tool.get("auth_type") == "app"]
        self.allowed_tools = {str(tool.get("name")) for tool in self.tool_catalog if tool.get("name")}
        if not self.tool_catalog:
            raise LLMError("FeishuToolPlanner 缺少应用工具 catalog")

    def plan(self, *, user_text: str, known_slots: dict[str, Any]) -> dict[str, Any]:
        system = (
            "你是飞书应用工具调用计划器。只输出 JSON，不直接回复用户，不执行工具。"
            "只能使用 tool_catalog 中 auth_type=app 的工具，不能请求用户授权工具，不能发明工具名。"
            "你需要自己决定工具调用流程。若要私聊/转达给某人但没有 user_id/open_id/chat_id，"
            "通常先 feishu.contact.search_user，再 feishu.im.create_chat，最后 feishu.im.send_message。"
            "变量引用只能使用字符串，如 $steps[0].data.items[0].user_id、$steps[1].data.chat_id。"
            "输出：{goal:string, success_message:string, steps:[{id:string, tool_name:string, args:object}]}。"
        )
        user = json.dumps({"user_text": user_text, "known_slots": known_slots, "tool_catalog": self.tool_catalog}, ensure_ascii=False)
        return self._normalize(self.llm_client.complete_json(system=system, user=user, schema=FEISHU_TOOL_PLAN_SCHEMA))

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        steps = data.get("steps")
        if not isinstance(steps, list) or not steps:
            raise LLMError("飞书工具计划缺少 steps")
        normalized: list[dict[str, Any]] = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise LLMError(f"飞书工具计划第 {i + 1} 步不是 object")
            tool_name = str(step.get("tool_name") or "").strip()
            if tool_name not in self.allowed_tools:
                raise LLMError(f"飞书工具计划使用了非应用工具或不存在的工具：{tool_name}")
            args = step.get("args") if isinstance(step.get("args"), dict) else {}
            normalized.append({"id": str(step.get("id") or f"step_{i + 1}"), "tool_name": tool_name, "args": args})
        return {"goal": str(data.get("goal") or "执行飞书应用工具计划"), "success_message": str(data.get("success_message") or "飞书应用工具计划已完成。"), "steps": normalized}
