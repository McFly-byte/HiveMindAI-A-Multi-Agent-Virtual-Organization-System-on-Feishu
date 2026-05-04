from __future__ import annotations

import json
import os
import threading
import traceback
from typing import Any

from ..events import Event, EventBus
from ..spec import EventSourceSpec


def _safe_get(obj: Any, *attrs: str, default: Any = None) -> Any:
    cur = obj
    for attr in attrs:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(attr, default)
        else:
            cur = getattr(cur, attr, default)
    return cur


def _json_loads_maybe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _marshal_lark(obj: Any) -> dict[str, Any]:
    try:
        import lark_oapi as lark
        raw = lark.JSON.marshal(obj)
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {"repr": repr(obj)}


def _extract_message_payload(data: Any) -> dict[str, Any]:
    raw = _marshal_lark(data)

    header = _safe_get(data, "header")
    event = _safe_get(data, "event")
    message = _safe_get(event, "message")
    sender = _safe_get(event, "sender")
    sender_id = _safe_get(sender, "sender_id")

    message_id = _safe_get(message, "message_id")
    chat_id = _safe_get(message, "chat_id")
    chat_type = _safe_get(message, "chat_type")
    message_type = _safe_get(message, "message_type")
    content = _json_loads_maybe(_safe_get(message, "content"))

    payload = {
        "event_id": _safe_get(header, "event_id") or _safe_get(raw, "header", "event_id"),
        "event_type": _safe_get(header, "event_type") or _safe_get(raw, "header", "event_type"),
        "message_id": message_id,
        "chat_id": chat_id,
        "chat_type": chat_type,
        "message_type": message_type,
        "content": content,
        "create_time": _safe_get(message, "create_time"),
        "sender": {
            "sender_id": _marshal_lark(sender_id) if sender_id is not None else None,
            "sender_type": _safe_get(sender, "sender_type"),
            "tenant_key": _safe_get(sender, "tenant_key"),
        },
        "raw": raw,
    }
    return payload


class FeishuWebSocketEventSource:
    """Feishu/Lark WebSocket event source.

    It converts SDK callback objects into internal EventBus events:
    - feishu.im.message.received
    - feishu.event.customized
    - feishu.ws.error

    The source deliberately does not call the LLM or tools directly.
    """

    def __init__(self, spec: EventSourceSpec, event_bus: EventBus) -> None:
        self.spec = spec
        self.name = spec.name
        self.event_bus = event_bus
        self.thread: threading.Thread | None = None
        self.client: Any | None = None
        self.running = False
        self.last_error: str | None = None
        self._seen_event_ids: set[str] = set()

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, name=f"tooltest-{self.name}", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False
        if self.client is not None:
            for method_name in ("stop", "close", "shutdown"):
                method = getattr(self.client, method_name, None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        pass
                    break

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.spec.type,
            "running": self.running,
            "thread_alive": bool(self.thread and self.thread.is_alive()),
            "last_error": self.last_error,
        }

    def _publish(self, event_type: str, payload: dict[str, Any], *, event_id: str | None = None) -> None:
        if event_id and event_id in self._seen_event_ids:
            return
        if event_id:
            self._seen_event_ids.add(event_id)
            # Bound the in-memory dedupe set for long sessions.
            if len(self._seen_event_ids) > 2000:
                self._seen_event_ids = set(list(self._seen_event_ids)[-1000:])

        kwargs = {"event_id": event_id} if event_id else {}
        self.event_bus.publish(Event(
            type=event_type,
            source=self.name,
            payload=payload,
            **kwargs,
        ))

    def _run(self) -> None:
        try:
            import lark_oapi as lark
        except Exception as e:
            self.last_error = "lark-oapi is not installed. Run: uv add lark-oapi"
            self._publish("feishu.ws.error", {"error": self.last_error, "detail": str(e)})
            return

        app_id = os.getenv(self.spec.config.get("app_id_env", "FEISHU_APP_ID"), "")
        app_secret = os.getenv(self.spec.config.get("app_secret_env", "FEISHU_APP_SECRET"), "")
        if not app_id or not app_secret:
            self.last_error = "missing FEISHU_APP_ID / FEISHU_APP_SECRET"
            self._publish("feishu.ws.error", {"error": self.last_error})
            return

        log_level_name = str(self.spec.config.get("log_level", "INFO")).upper()
        log_level = getattr(lark.LogLevel, log_level_name, lark.LogLevel.INFO)

        def on_message_receive(data: Any) -> None:
            try:
                payload = _extract_message_payload(data)
                event_id = payload.get("event_id") or payload.get("message_id")
                self._publish("feishu.im.message.received", payload, event_id=event_id)
            except Exception as e:
                self._publish("feishu.ws.error", {"error": str(e), "traceback": traceback.format_exc()})

        def on_customized_event(data: Any) -> None:
            try:
                raw = _marshal_lark(data)
                event_id = _safe_get(raw, "header", "event_id")
                self._publish("feishu.event.customized", {"raw": raw}, event_id=event_id)
            except Exception as e:
                self._publish("feishu.ws.error", {"error": str(e), "traceback": traceback.format_exc()})

        builder = lark.EventDispatcherHandler.builder(
            self.spec.config.get("verification_token", ""),
            self.spec.config.get("encrypt_key", ""),
        ).register_p2_im_message_receive_v1(on_message_receive)

        customized = self.spec.config.get("customized_events", []) or []
        for key in customized:
            builder = builder.register_p1_customized_event(key, on_customized_event)

        event_handler = builder.build()
        self.client = lark.ws.Client(app_id, app_secret, event_handler=event_handler, log_level=log_level)
        self._publish("feishu.ws.started", {"message": "Feishu WebSocket event source started"})
        try:
            self.client.start()
        except Exception as e:
            self.last_error = str(e)
            self._publish("feishu.ws.error", {"error": str(e), "traceback": traceback.format_exc()})
