from __future__ import annotations

import json
from typing import Any


def to_hive_event(raw_event: dict[str, Any], *, project_id: str, target_agent_id: str | None = "orchestrator") -> dict[str, Any] | None:
    event_type = str(raw_event.get("event_type") or "")
    if event_type != "feishu.im.message.received":
        return None

    payload = _normalize_message_payload(raw_event.get("payload"))
    return {
        "event_type": "feishu.im.message.received",
        "project_id": project_id,
        "source": "feishu_adapter",
        "target_agent_id": target_agent_id,
        "payload": payload,
        "metadata": {"adapter_event": raw_event},
    }


def _normalize_message_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"raw": payload}
    raw = payload.get("raw")
    if not isinstance(raw, dict):
        return payload
    event = raw.get("event") if isinstance(raw.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
    content = message.get("content")
    return {
        "raw": raw,
        "text": _extract_text(content),
        "chat_id": message.get("chat_id"),
        "chat_type": message.get("chat_type"),
        "message_id": message.get("message_id"),
        "message_type": message.get("message_type"),
        "sender_type": sender.get("sender_type"),
        "sender_open_id": sender_id.get("open_id"),
        "sender_user_id": sender_id.get("user_id"),
    }


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return content
        if isinstance(parsed, dict):
            return str(parsed.get("text") or parsed.get("content") or content)
        return content
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "")
    return ""
