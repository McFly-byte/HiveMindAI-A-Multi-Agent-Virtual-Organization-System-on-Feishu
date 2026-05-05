from __future__ import annotations

from copy import deepcopy
import importlib
import json
import uuid
from typing import Any

from tooltest.events import EventBus
from tooltest.tools import ToolRegistry, ToolSpec

from tools import feishu_message_tools as legacy_message_tools


SOURCE_TOOL_MODULES = [
    "tools.eval",
    "tools.feishu_bitable",
    "tools.feishu_contact",
    "tools.feishu_docx",
    "tools.feishu_drive",
    "tools.feishu_im_group",
    "tools.feishu_im_message",
    "tools.feishu_wiki",
]


def _register_existing_module(registry: ToolRegistry, module_name: str) -> None:
    module = importlib.import_module(module_name)
    source_registry = ToolRegistry()
    module.register(source_registry)

    for source_tool in source_registry.list():
        spec = deepcopy(source_tool.spec)

        @registry.register(spec)
        def wrapped_tool(args: dict[str, Any], ctx: Any, func=source_tool.func) -> dict[str, Any]:
            return func(args, ctx)


def register(registry: ToolRegistry, event_bus: EventBus | None = None):
    for module_name in SOURCE_TOOL_MODULES:
        _register_existing_module(registry, module_name)

    @registry.register(
        ToolSpec(
            name="feishu_send_text",
            description="Send a text message to a Feishu chat_id.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "text": {"type": "string"},
                    "uuid": {"type": "string"},
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

        _, client = legacy_message_tools._client()
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

        resp = client.im.v1.message.create(req)
        result = legacy_message_tools._resp_to_dict(resp)
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
                    "message_id": {"type": "string"},
                    "text": {"type": "string"},
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

        _, client = legacy_message_tools._client()
        content = json.dumps({"text": args["text"]}, ensure_ascii=False)
        req = (
            ReplyMessageRequest.builder()
            .message_id(args["message_id"])
            .request_body(ReplyMessageRequestBody.builder().msg_type("text").content(content).build())
            .build()
        )

        resp = client.im.v1.message.reply(req)
        result = legacy_message_tools._resp_to_dict(resp)
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

    return "feishu_integration"
