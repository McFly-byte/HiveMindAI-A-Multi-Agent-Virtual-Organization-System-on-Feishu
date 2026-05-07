from __future__ import annotations

import asyncio
import json
from typing import Any

from agent_hive.events.models import HiveEvent
from agent_hive.events.sources.base import EventEmitFn
from agent_hive.observability.logging import get_logger
from agent_hive.tools.providers.feishu import FeishuProvider


logger = get_logger("events.feishu_im")


class FeishuIMEventSource:
    """Polls Feishu adapter events and emits normalized HiveEvents.

    The existing Feishu adapter starts its WebSocket listener when
    ``FEISHU_ENABLE_IM_WS=1`` and the adapter module is registered with an
    event bus. This source only bridges those adapter events into ``HiveEvent``.
    It does not change the adapter's calling logic.
    """

    name = "feishu_im"

    def __init__(
        self,
        provider: FeishuProvider,
        *,
        project_id: str,
        target_agent_id: str | None = "orchestrator",
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self.provider = provider
        self.project_id = project_id
        self.target_agent_id = target_agent_id
        self.poll_interval_seconds = poll_interval_seconds
        self._stop = asyncio.Event()

    async def run(self, emit: EventEmitFn) -> None:
        logger.info(
            "starting Feishu IM event bridge project_id=%s target_agent=%s",
            self.project_id,
            self.target_agent_id,
        )
        self.provider.enable_im_websocket()
        self.provider.load()
        logger.info("Feishu provider loaded; IM WebSocket starts from adapter tool registration")
        while not self._stop.is_set():
            for raw_event in self.provider.drain_events():
                logger.debug(
                    "Feishu adapter event received event_type=%s source=%s",
                    raw_event.get("event_type"),
                    raw_event.get("source"),
                )
                if raw_event.get("event_type") == "feishu.ws.started":
                    logger.info("Feishu IM WebSocket started app_id=%s", raw_event.get("payload", {}).get("app_id"))
                    continue
                if raw_event.get("event_type") == "feishu.ws.error":
                    logger.error("Feishu IM WebSocket error: %s", raw_event.get("payload", {}).get("error"))
                    continue
                event = self._normalize(raw_event)
                if event is not None:
                    msg = _message_brief(event.payload)
                    logger.info(
                        "normalized Feishu IM message event event_id=%s target_agent=%s chat_id=%s message_id=%s text=%s",
                        event.event_id,
                        event.target_agent_id,
                        msg.get("chat_id"),
                        msg.get("message_id"),
                        msg.get("text"),
                    )
                    await emit(event)
            await asyncio.sleep(self.poll_interval_seconds)

    async def stop(self) -> None:
        logger.info("stopping Feishu IM WebSocket source")
        self._stop.set()

    def _normalize(self, raw_event: dict[str, Any]) -> HiveEvent | None:
        event_type = str(raw_event.get("event_type") or "")
        if event_type != "feishu.im.message.received":
            return None
        payload = _normalize_message_payload(raw_event.get("payload"))
        return HiveEvent(
            event_type="feishu.im.message.received",
            project_id=self.project_id,
            source="feishu_im",
            target_agent_id=self.target_agent_id,
            payload=payload,
            metadata={"adapter_event": raw_event},
        )


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
    text = _extract_text(content)
    return {
        "raw": raw,
        "text": text,
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


def _message_brief(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "chat_id": payload.get("chat_id"),
        "message_id": payload.get("message_id"),
        "text": payload.get("text"),
    }
