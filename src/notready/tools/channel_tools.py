from __future__ import annotations

from typing import Any

from tools.registry import ToolRegistry
from tools.spec import ToolSpec


class WebChannelToolAdapter:
    """Application tool adapter for Web UI outbound messages.

    WebUI is treated as just another channel. Runtime code must not directly
    construct an assistant reply for the browser; it asks ToolExecutor to call
    channel.web.send_message and returns that tool result to Flask.
    """

    def register(self, registry: ToolRegistry) -> None:
        registry.register(
            ToolSpec(
                name="channel.web.send_message",
                description="应用工具：向 WebUI 当前会话发送一条完整文本消息。非流式。",
                args_schema={
                    "type": "object",
                    "required": ["session_id", "text"],
                    "properties": {
                        "session_id": {"type": "string"},
                        "text": {"type": "string"},
                        "user_id": {"type": "string"},
                    },
                },
                side_effect=True,
                handler=self._send_message,
                auth_type="app",
            )
        )

    @staticmethod
    def _send_message(args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text") or "")
        session_id = str(args.get("session_id") or "")
        return {
            "channel": "web",
            "delivered": True,
            "session_id": session_id,
            "user_id": args.get("user_id"),
            "text": text,
        }
