from __future__ import annotations

from typing import Any, Callable


class ChannelOpsAgent:
    """Tool agent for every outbound channel message.

    FrontDeskAgent decides *what* to say. ChannelOpsAgent decides *which channel
    tool* sends it. It is intentionally deterministic: no LLM is needed to reply
    to the current user once FrontDeskAgent has produced the text.
    """

    id = "channel_ops_agent"
    name = "通道操作 Agent"
    tag = "出口工具 Agent"
    lead = "统一发送 WebUI / 飞书等通道回复；所有对外消息都必须经过 ToolExecutor。"
    icon = "通"
    accent = "#2563eb"

    def __init__(self, *, tool_call: Callable[[str, dict, str, str | None, str | None], dict]) -> None:
        self.tool_call = tool_call

    def ui_meta(self) -> dict[str, Any]:
        return {
            "key": self.id,
            "name": self.name,
            "tag": self.tag,
            "lead": self.lead,
            "icon": self.icon,
            "accent": self.accent,
            "chips": ["outbound", "web", "feishu", "tool-call"],
        }

    def send_reply(
        self,
        *,
        channel: str,
        text: str,
        session_id: str | None,
        dialogue_id: str | None = None,
        chat_id: str | None = None,
        user_id: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> dict[str, Any]:
        channel = (channel or "web").strip().lower()
        if channel == "web":
            return self.tool_call(
                "channel.web.send_message",
                {"session_id": session_id or "", "user_id": user_id or "", "text": text},
                self.id,
                session_id,
                dialogue_id,
            )

        if channel == "feishu":
            if reply_to_message_id:
                return self.tool_call(
                    "feishu.im.reply_message",
                    {"message_id": reply_to_message_id, "text": text},
                    self.id,
                    session_id,
                    dialogue_id,
                )
            return self.tool_call(
                "feishu.im.send_message",
                {"chat_id": chat_id or user_id or "", "receive_id": chat_id or user_id or "", "text": text},
                self.id,
                session_id,
                dialogue_id,
            )

        return {"ok": False, "error": f"unsupported outbound channel: {channel}"}
