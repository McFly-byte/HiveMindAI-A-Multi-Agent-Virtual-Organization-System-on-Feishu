from __future__ import annotations

import json
import os
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any

from .events import Event, EventBus


@dataclass
class SourceStatus:
    name: str
    type: str
    enabled: bool = False
    running: bool = False
    last_error: str | None = None


class FeishuWsSource:
    def __init__(self, name: str, event_bus: EventBus, app_id_env: str = "FEISHU_APP_ID", app_secret_env: str = "FEISHU_APP_SECRET"):
        self.name = name
        self.event_bus = event_bus
        self.app_id_env = app_id_env
        self.app_secret_env = app_secret_env
        self.status = SourceStatus(name=name, type="feishu.websocket", enabled=True)
        self._thread: threading.Thread | None = None
        self._client = None

    def start(self):
        if self.status.running:
            return
        app_id = os.getenv(self.app_id_env, "")
        app_secret = os.getenv(self.app_secret_env, "")
        if not app_id or not app_secret:
            self.status.last_error = f"missing env {self.app_id_env}/{self.app_secret_env}"
            self.event_bus.publish(Event(type="feishu.ws.error", source=self.name, payload={"error": self.status.last_error}))
            return

        def run():
            try:
                import lark_oapi as lark
                from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

                def on_message(data: P2ImMessageReceiveV1):
                    raw_text = lark.JSON.marshal(data, indent=2)
                    try:
                        raw = json.loads(raw_text)
                    except Exception:
                        raw = {"raw": raw_text}
                    payload = normalize_feishu_message_event(raw)
                    payload["raw"] = raw
                    self.event_bus.publish(Event(type="feishu.im.message.received", source=self.name, payload=payload))

                handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message).build()
                self._client = lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.INFO)
                self.status.running = True
                self.event_bus.publish(Event(type="feishu.ws.started", source=self.name, payload={"name": self.name}))
                self._client.start()
            except Exception as e:
                self.status.running = False
                self.status.last_error = str(e)
                self.event_bus.publish(Event(type="feishu.ws.error", source=self.name, payload={"error": str(e), "traceback": traceback.format_exc()}))

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        # lark ws client does not expose a consistently documented stop across SDK versions.
        self.status.running = False
        self.event_bus.publish(Event(type="feishu.ws.stopped", source=self.name, payload={"name": self.name}))

    def to_dict(self) -> dict[str, Any]:
        return self.status.__dict__.copy()


def normalize_feishu_message_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Best-effort normalizer; keeps raw event too."""
    event = raw.get("event") or raw.get("data", {}).get("event") or raw
    message = event.get("message") or {}
    sender = event.get("sender") or {}
    content_raw = message.get("content") or ""
    content: dict[str, Any] = {}
    if isinstance(content_raw, str):
        try:
            content = json.loads(content_raw)
        except Exception:
            content = {"text": content_raw}
    elif isinstance(content_raw, dict):
        content = content_raw
    return {
        "message_id": message.get("message_id") or event.get("message_id") or "",
        "chat_id": message.get("chat_id") or "",
        "chat_type": message.get("chat_type") or "",
        "sender_id": sender.get("sender_id", {}).get("open_id") or sender.get("sender_id", {}).get("user_id") or "",
        "message_type": message.get("message_type") or "",
        "content": content,
        "create_time": message.get("create_time") or "",
    }
