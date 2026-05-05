from __future__ import annotations

import json
import os
import threading
import uuid
from typing import Any

from tool_integration.events import EventBus, ToolIntegrationEvent
from tool_integration.tools import ToolRegistry, ToolSpec


def _client():
    try:
        import lark_oapi as lark
    except Exception as e:
        raise RuntimeError("lark-oapi is not installed. Run: uv add lark-oapi") from e

    app_id = os.getenv("FEISHU_APP_ID", "")
    app_secret = os.getenv("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        raise RuntimeError("missing FEISHU_APP_ID / FEISHU_APP_SECRET in .env")

    client = lark.Client.builder() \
        .app_id(app_id) \
        .app_secret(app_secret) \
        .log_level(lark.LogLevel.INFO) \
        .build()
    return lark, client


_lark_and_client_cache: tuple[Any, Any] | None = None


def _get_lark_and_client() -> tuple[Any, Any]:
    """Lazily build Feishu client so :func:`register` succeeds without APP_ID in CI/tests."""
    global _lark_and_client_cache
    if _lark_and_client_cache is None:
        _lark_and_client_cache = _client()
    return _lark_and_client_cache


def _resp_to_dict(resp: Any) -> dict[str, Any]:
    try:
        import lark_oapi as lark

        raw = lark.JSON.marshal(resp, indent=2)
        raw_obj = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        raw_obj = {"repr": repr(resp)}

    try:
        ok = bool(resp.success())
    except Exception:
        ok = bool(getattr(resp, "code", 1) == 0)

    data = getattr(resp, "data", None)
    message_id = None
    if data is not None:
        message_id = getattr(data, "message_id", None) or getattr(data, "message_id_str", None)

    return {
        "ok": ok,
        "code": getattr(resp, "code", None),
        "msg": getattr(resp, "msg", None),
        "log_id": resp.get_log_id() if hasattr(resp, "get_log_id") else None,
        "message_id": message_id,
        "raw": raw_obj,
    }


_ws_started = False
_ws_lock = threading.Lock()


def _start_im_listener(lark: Any, event_bus: EventBus):
    global _ws_started
    with _ws_lock:
        if _ws_started:
            return
        _ws_started = True

    app_id = os.getenv("FEISHU_APP_ID", "")
    app_secret = os.getenv("FEISHU_APP_SECRET", "")

    def do_p2_im_message_receive_v1(data: Any) -> None:
        try:
            raw = lark.JSON.marshal(data, indent=2)
            raw_obj = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            raw_obj = {"repr": repr(data)}
        event_bus.publish(ToolIntegrationEvent(event_type="feishu.im.message.received", source="feishu.ws", payload={"raw": raw_obj}))

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
        .build()
    )

    def _run_ws():
        try:
            event_bus.publish(ToolIntegrationEvent(event_type="feishu.ws.started", source="feishu.ws", payload={"app_id": app_id}))
            cli = lark.ws.Client(app_id, app_secret, event_handler=event_handler, log_level=lark.LogLevel.DEBUG)
            cli.start()
        except Exception as e:
            event_bus.publish(ToolIntegrationEvent(event_type="feishu.ws.error", source="feishu.ws", payload={"error": str(e)}))

    threading.Thread(target=_run_ws, daemon=True, name="feishu-im-ws").start()


def register(registry: ToolRegistry, event_bus: EventBus | None = None):
    # WebSocket 长连接会在独立线程里跑 asyncio，与 ``asyncio.run()`` 主链路冲突（报 event loop is already running）。
    # MVP / 批处理场景默认不启动；需要 IM 事件流时再设置 FEISHU_ENABLE_IM_WS=1。
    ws_on = os.environ.get("FEISHU_ENABLE_IM_WS", "").strip().lower() in ("1", "true", "yes")
    if event_bus is not None and ws_on:
        try:
            lark, _ = _get_lark_and_client()
            _start_im_listener(lark, event_bus)
        except RuntimeError:
            pass

    @registry.register(
        ToolSpec(
            name="feishu_send_text",
            description="Send a text message to a Feishu chat_id.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string", "description": "Feishu chat id"},
                    "text": {"type": "string", "description": "Text to send"},
                    "uuid": {"type": "string", "description": "Optional dedupe UUID"},
                },
                "required": ["chat_id", "text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "code": {"type": ["integer", "null"]},
                    "msg": {"type": ["string", "null"]},
                    "log_id": {"type": ["string", "null"]},
                    "message_id": {"type": ["string", "null"]},
                    "raw": {"type": "object"},
                },
                "required": ["ok", "raw"],
            },
        )
    )
    def feishu_send_text(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        content = json.dumps({"text": args["text"]}, ensure_ascii=False)
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(args["chat_id"])
                .msg_type("text")
                .content(content)
                .uuid(args.get("uuid") or str(uuid.uuid4()))
                .build()
            )
            .build()
        )

        _, client = _get_lark_and_client()
        resp = client.im.v1.message.create(req)
        result = _resp_to_dict(resp)
        ctx.emit(
            "feishu.message.sent",
            {
                "tool": "feishu_send_text",
                "ok": result["ok"],
                "message_id": result.get("message_id"),
                "chat_id": args["chat_id"],
            },
        )
        return result

    @registry.register(
        ToolSpec(
            name="feishu_reply_text",
            description="Reply text to a Feishu message_id.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "Feishu message id"},
                    "text": {"type": "string", "description": "Reply text"},
                },
                "required": ["message_id", "text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "code": {"type": ["integer", "null"]},
                    "msg": {"type": ["string", "null"]},
                    "log_id": {"type": ["string", "null"]},
                    "message_id": {"type": ["string", "null"]},
                    "raw": {"type": "object"},
                },
                "required": ["ok", "raw"],
            },
        )
    )
    def feishu_reply_text(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

        content = json.dumps({"text": args["text"]}, ensure_ascii=False)
        req = (
            ReplyMessageRequest.builder()
            .message_id(args["message_id"])
            .request_body(ReplyMessageRequestBody.builder().msg_type("text").content(content).build())
            .build()
        )

        _, client = _get_lark_and_client()
        resp = client.im.v1.message.reply(req)
        result = _resp_to_dict(resp)
        ctx.emit(
            "feishu.message.replied",
            {
                "tool": "feishu_reply_text",
                "ok": result["ok"],
                "message_id": result.get("message_id"),
                "reply_to": args["message_id"],
            },
        )
        return result
