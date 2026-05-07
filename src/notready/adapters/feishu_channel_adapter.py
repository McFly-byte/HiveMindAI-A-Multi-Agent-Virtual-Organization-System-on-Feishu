from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from runtime.models import Event


@dataclass(frozen=True)
class FeishuInboundMessage:
    event_id: str | None
    message_id: str | None
    chat_id: str
    chat_type: str
    user_id: str
    open_id: str | None
    union_id: str | None
    message_type: str
    text: str
    mention_keys: list[str]
    is_mentioned: bool
    raw: dict[str, Any]


class FeishuChannelAdapter:
    """飞书通道适配器：只负责消息进出，不做业务判断。"""

    MESSAGE_EVENT_TYPES = {"im.message.receive_v1"}

    def __init__(self, *, group_requires_mention: bool = False) -> None:
        self.group_requires_mention = group_requires_mention

    @staticmethod
    def extract_challenge(raw_event: dict[str, Any]) -> str | None:
        if not isinstance(raw_event, dict):
            return None
        if raw_event.get("type") == "url_verification" and raw_event.get("challenge"):
            return str(raw_event["challenge"])
        header = raw_event.get("header") if isinstance(raw_event.get("header"), dict) else {}
        if header.get("event_type") == "url_verification" and raw_event.get("challenge"):
            return str(raw_event["challenge"])
        return None

    @staticmethod
    def is_message_event(raw_event: dict[str, Any]) -> bool:
        header = raw_event.get("header") if isinstance(raw_event.get("header"), dict) else {}
        event_type = str(header.get("event_type") or raw_event.get("type") or "")
        if not event_type and isinstance(raw_event.get("event"), dict):
            return isinstance(raw_event["event"].get("message"), dict)
        return event_type in FeishuChannelAdapter.MESSAGE_EVENT_TYPES

    def parse_im_message(self, raw_event: dict[str, Any]) -> FeishuInboundMessage:
        event = raw_event.get("event") if isinstance(raw_event.get("event"), dict) else raw_event
        message = event.get("message") if isinstance(event.get("message"), dict) else raw_event.get("message", {})
        sender = event.get("sender") if isinstance(event.get("sender"), dict) else raw_event.get("sender", {})
        header = raw_event.get("header") if isinstance(raw_event.get("header"), dict) else {}

        sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
        user_id = (
            sender_id.get("user_id")
            or sender.get("user_id")
            or sender_id.get("open_id")
            or sender.get("open_id")
            or "unknown_user"
        )
        open_id = sender_id.get("open_id") or sender.get("open_id")
        union_id = sender_id.get("union_id") or sender.get("union_id")
        chat_id = message.get("chat_id") or message.get("chat_id_str") or raw_event.get("chat_id") or "unknown_chat"
        chat_type = str(message.get("chat_type") or raw_event.get("chat_type") or "unknown")
        message_id = message.get("message_id") or message.get("message_id_str") or raw_event.get("message_id")
        event_id = header.get("event_id") or raw_event.get("event_id")
        message_type = str(message.get("message_type") or message.get("msg_type") or raw_event.get("message_type") or "text")

        text = self._extract_text(message.get("content") if "content" in message else raw_event.get("content"))
        mentions = message.get("mentions") if isinstance(message.get("mentions"), list) else []
        mention_keys = [str(m.get("key")) for m in mentions if isinstance(m, dict) and m.get("key")]
        stripped_text = self._strip_mention_keys(text, mention_keys)
        is_mentioned = bool(mention_keys) or stripped_text != text

        return FeishuInboundMessage(
            event_id=str(event_id) if event_id else None,
            message_id=str(message_id) if message_id else None,
            chat_id=str(chat_id),
            chat_type=chat_type,
            user_id=str(user_id),
            open_id=str(open_id) if open_id else None,
            union_id=str(union_id) if union_id else None,
            message_type=message_type,
            text=stripped_text,
            mention_keys=mention_keys,
            is_mentioned=is_mentioned,
            raw=raw_event,
        )

    def should_process(self, msg: FeishuInboundMessage) -> tuple[bool, str | None]:
        if msg.message_type != "text":
            return False, f"暂不处理飞书 {msg.message_type} 消息，仅接入 text。"
        if not msg.text.strip():
            return False, "飞书文本消息为空。"
        if self.group_requires_mention and msg.chat_type == "group" and not msg.is_mentioned:
            return False, "群聊消息未 @ 机器人，忽略。"
        return True, None

    def handle_im_event(self, raw_event: dict[str, Any]) -> Event:
        msg = self.parse_im_message(raw_event)
        ok, ignore_reason = self.should_process(msg)
        return Event(
            event_type="channel.message.received",
            source="feishu_channel",
            target="frontdesk_agent",
            session_id=self.session_id_for(msg),
            payload={
                "channel": "feishu",
                "chat_id": msg.chat_id,
                "chat_type": msg.chat_type,
                "user_id": msg.user_id,
                "open_id": msg.open_id,
                "union_id": msg.union_id,
                "message_id": msg.message_id,
                "feishu_event_id": msg.event_id,
                "message_type": msg.message_type,
                "text": msg.text,
                "mention_keys": msg.mention_keys,
                "is_mentioned": msg.is_mentioned,
                "process": ok,
                "ignore_reason": ignore_reason,
                "raw": raw_event,
            },
        )

    @staticmethod
    def session_id_for(msg: FeishuInboundMessage) -> str:
        return f"feishu:{msg.chat_id}:{msg.user_id}"

    def send_message(self, event: Event) -> dict[str, Any]:
        payload = event.payload or {}
        return {
            "ok": True,
            "channel": "feishu",
            "receive_id": payload.get("chat_id") or payload.get("user_id"),
            "message_id": payload.get("reply_to_message_id"),
            "text": payload.get("text", ""),
            "payload": payload,
        }

    @staticmethod
    def _extract_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, dict):
            return str(content.get("text") or content.get("content") or "")
        if not isinstance(content, str):
            return str(content)
        value = content.strip()
        if not value:
            return ""
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        if isinstance(parsed, dict):
            text = parsed.get("text")
            if isinstance(text, str):
                return text
            for key in ("content", "title"):
                if isinstance(parsed.get(key), str):
                    return parsed[key]
        return value

    @staticmethod
    def _strip_mention_keys(text: str, mention_keys: list[str]) -> str:
        result = text or ""
        for key in mention_keys:
            if key:
                result = result.replace(key, "")
        result = re.sub(r"@_user_\d+", "", result)
        return re.sub(r"\s+", " ", result).strip()
