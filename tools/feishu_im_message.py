from __future__ import annotations

import json
from typing import Any

from tooltest.tools import ToolRegistry, ToolSpec
from tooltest.events import EventBus

from tools.feishu_client import feishu_request


DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


def _first_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, int):
            return str(value)
    return ""


def _as_bool(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else False


def _page_size(args: dict[str, Any], default: int = DEFAULT_PAGE_SIZE) -> int:
    value = args.get("page_size", default)
    try:
        size = int(value)
    except Exception:
        size = default
    return max(1, min(size, MAX_PAGE_SIZE))


def _items(data: dict[str, Any]) -> list[dict[str, Any]]:
    value = data.get("items") or data.get("messages") or []
    return value if isinstance(value, list) else []


def _message_from_data(data: dict[str, Any]) -> dict[str, Any]:
    items = _items(data)
    if items and isinstance(items[0], dict):
        return items[0]
    return data


def _sender(data: dict[str, Any]) -> dict[str, Any]:
    sender = data.get("sender")
    if not isinstance(sender, dict):
        return {}
    return {
        "id": _first_str(sender, "id"),
        "id_type": _first_str(sender, "id_type"),
        "sender_type": _first_str(sender, "sender_type"),
        "tenant_key": _first_str(sender, "tenant_key"),
    }


def _content_string(data: dict[str, Any]) -> str:
    body = data.get("body")
    if isinstance(body, dict):
        value = body.get("content")
        if isinstance(value, str):
            return value
        if value is not None:
            return json.dumps(value, ensure_ascii=False)
    value = data.get("content")
    if isinstance(value, str):
        return value
    if value is not None:
        return json.dumps(value, ensure_ascii=False)
    return ""


def _flatten_text(value: Any) -> list[str]:
    parts: list[str] = []

    if isinstance(value, str):
        if value:
            parts.append(value)
    elif isinstance(value, list):
        for item in value:
            parts.extend(_flatten_text(item))
    elif isinstance(value, dict):
        for key in ("text", "title", "summary", "file_name", "name"):
            child = value.get(key)
            if isinstance(child, str) and child:
                parts.append(child)
        for key in ("content", "elements", "children"):
            if key in value:
                parts.extend(_flatten_text(value[key]))

    return parts


def _extract_text(content: str) -> str:
    if not content:
        return ""

    try:
        value = json.loads(content)
    except Exception:
        return content

    return "\n".join(part for part in _flatten_text(value) if part).strip()


def _mentions(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("mentions")
    if not isinstance(raw, list):
        return []

    items: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "key": _first_str(item, "key"),
                "id": _first_str(item, "id"),
                "id_type": _first_str(item, "id_type"),
                "name": _first_str(item, "name"),
                "tenant_key": _first_str(item, "tenant_key"),
            }
        )
    return items


def _normalize_message(data: dict[str, Any]) -> dict[str, Any]:
    content = _content_string(data)
    return {
        "message_id": _first_str(data, "message_id"),
        "root_id": _first_str(data, "root_id"),
        "parent_id": _first_str(data, "parent_id"),
        "thread_id": _first_str(data, "thread_id"),
        "upper_message_id": _first_str(data, "upper_message_id"),
        "chat_id": _first_str(data, "chat_id"),
        "msg_type": _first_str(data, "msg_type"),
        "create_time": _first_str(data, "create_time"),
        "update_time": _first_str(data, "update_time"),
        "deleted": _as_bool(data.get("deleted")),
        "updated": _as_bool(data.get("updated")),
        "sender": _sender(data),
        "content": content,
        "text": _extract_text(content),
        "mentions": _mentions(data),
    }


def _normalize_messages(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_normalize_message(item) for item in items if isinstance(item, dict)]


def _message_result_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "message_id": {"type": "string"},
            "root_id": {"type": "string"},
            "parent_id": {"type": "string"},
            "thread_id": {"type": "string"},
            "upper_message_id": {"type": "string"},
            "chat_id": {"type": "string"},
            "msg_type": {"type": "string"},
            "create_time": {"type": "string"},
            "update_time": {"type": "string"},
            "deleted": {"type": "boolean"},
            "updated": {"type": "boolean"},
            "sender": {"type": "object"},
            "content": {"type": "string"},
            "text": {"type": "string"},
            "mentions": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["message_id", "chat_id", "msg_type", "content", "text"],
        "additionalProperties": False,
    }


def _message_id_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "message_id": {"type": "string"},
            "root_id": {"type": "string"},
            "parent_id": {"type": "string"},
            "thread_id": {"type": "string"},
            "chat_id": {"type": "string"},
            "msg_type": {"type": "string"},
            "create_time": {"type": "string"},
        },
        "required": ["message_id"],
        "additionalProperties": False,
    }


def _to_seconds(value: str) -> int:
    if not value:
        return 0
    try:
        timestamp = int(value)
    except Exception:
        return 0
    if timestamp > 9_999_999_999:
        timestamp //= 1000
    return timestamp


def _list_messages(
    *,
    container_id_type: str,
    container_id: str,
    page_size: int,
    page_token: str = "",
    start_time: str | int | None = None,
    end_time: str | int | None = None,
    sort_type: str = "ByCreateTimeDesc",
    card_msg_content_type: str = "",
) -> dict[str, Any]:
    queries: dict[str, Any] = {
        "container_id_type": container_id_type,
        "container_id": container_id,
        "page_size": page_size,
        "page_token": page_token,
        "start_time": start_time,
        "end_time": end_time,
        "sort_type": sort_type,
        "card_msg_content_type": card_msg_content_type,
    }

    data = feishu_request(
        "GET",
        "/open-apis/im/v1/messages",
        queries=queries,
    )

    items = _normalize_messages(_items(data))
    return {
        "items": items,
        "has_more": bool(data.get("has_more", False)),
        "page_token": data.get("page_token") or data.get("next_page_token") or "",
    }


def register(registry: ToolRegistry, event_bus: EventBus | None = None):
    @registry.register(
        ToolSpec(
            name="feishu_im_get_message",
            description="Get one Feishu IM message by message_id.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                },
                "required": ["message_id"],
                "additionalProperties": False,
            },
            output_schema=_message_result_schema(),
        )
    )
    def feishu_im_get_message(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        message_id = args["message_id"]

        data = feishu_request(
            "GET",
            f"/open-apis/im/v1/messages/{message_id}",
        )

        result = _normalize_message(_message_from_data(data))

        ctx.emit(
            "feishu.im.message.got",
            {
                "tool": "feishu_im_get_message",
                "message_id": result["message_id"] or message_id,
                "chat_id": result["chat_id"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_list_messages",
            description="List Feishu IM messages in a chat or thread container.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "container_id_type": {
                        "type": "string",
                        "enum": ["chat", "thread"],
                    },
                    "container_id": {"type": "string"},
                    "start_time": {
                        "type": "string",
                        "description": "Unix seconds timestamp. Optional.",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "Unix seconds timestamp. Optional.",
                    },
                    "sort_type": {
                        "type": "string",
                        "enum": ["ByCreateTimeAsc", "ByCreateTimeDesc"],
                    },
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                    "card_msg_content_type": {"type": "string"},
                },
                "required": ["container_id_type", "container_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": _message_result_schema()},
                    "has_more": {"type": "boolean"},
                    "page_token": {"type": "string"},
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_list_messages(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        container_id_type = args["container_id_type"]
        container_id = args["container_id"]

        result = _list_messages(
            container_id_type=container_id_type,
            container_id=container_id,
            start_time=args.get("start_time"),
            end_time=args.get("end_time"),
            sort_type=args.get("sort_type") or "ByCreateTimeDesc",
            page_size=_page_size(args),
            page_token=args.get("page_token") or "",
            card_msg_content_type=args.get("card_msg_content_type") or "",
        )

        ctx.emit(
            "feishu.im.messages.listed",
            {
                "tool": "feishu_im_list_messages",
                "container_id_type": container_id_type,
                "container_id": container_id,
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_list_chat_messages",
            description="List recent messages in a Feishu chat by chat_id.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "start_time": {"type": "string"},
                    "end_time": {"type": "string"},
                    "sort_type": {
                        "type": "string",
                        "enum": ["ByCreateTimeAsc", "ByCreateTimeDesc"],
                    },
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                    "card_msg_content_type": {"type": "string"},
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": _message_result_schema()},
                    "has_more": {"type": "boolean"},
                    "page_token": {"type": "string"},
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_list_chat_messages(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        chat_id = args["chat_id"]

        result = _list_messages(
            container_id_type="chat",
            container_id=chat_id,
            start_time=args.get("start_time"),
            end_time=args.get("end_time"),
            sort_type=args.get("sort_type") or "ByCreateTimeDesc",
            page_size=_page_size(args),
            page_token=args.get("page_token") or "",
            card_msg_content_type=args.get("card_msg_content_type") or "",
        )

        ctx.emit(
            "feishu.im.chat_messages.listed",
            {
                "tool": "feishu_im_list_chat_messages",
                "chat_id": chat_id,
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_list_thread_messages",
            description="List messages in a Feishu thread by thread_id.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "sort_type": {
                        "type": "string",
                        "enum": ["ByCreateTimeAsc", "ByCreateTimeDesc"],
                    },
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                    "card_msg_content_type": {"type": "string"},
                },
                "required": ["thread_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "items": {"type": "array", "items": _message_result_schema()},
                    "has_more": {"type": "boolean"},
                    "page_token": {"type": "string"},
                },
                "required": ["thread_id", "items"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_list_thread_messages(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        thread_id = args["thread_id"]

        result = _list_messages(
            container_id_type="thread",
            container_id=thread_id,
            sort_type=args.get("sort_type") or "ByCreateTimeAsc",
            page_size=_page_size(args),
            page_token=args.get("page_token") or "",
            card_msg_content_type=args.get("card_msg_content_type") or "",
        )
        result = {"thread_id": thread_id, **result}

        ctx.emit(
            "feishu.im.thread_messages.listed",
            {
                "tool": "feishu_im_list_thread_messages",
                "thread_id": thread_id,
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_get_message_context",
            description="Get an anchor message and nearby chat or thread messages for agent context review.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                    "prefer_thread": {"type": "boolean"},
                    "before_seconds": {"type": "integer"},
                    "after_seconds": {"type": "integer"},
                    "page_size": {"type": "integer"},
                    "card_msg_content_type": {"type": "string"},
                },
                "required": ["message_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "anchor_message": _message_result_schema(),
                    "container_id_type": {"type": "string"},
                    "container_id": {"type": "string"},
                    "items": {"type": "array", "items": _message_result_schema()},
                    "has_more": {"type": "boolean"},
                    "page_token": {"type": "string"},
                },
                "required": ["anchor_message", "container_id_type", "container_id", "items"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_get_message_context(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        message_id = args["message_id"]

        data = feishu_request(
            "GET",
            f"/open-apis/im/v1/messages/{message_id}",
        )
        anchor = _normalize_message(_message_from_data(data))

        prefer_thread = bool(args.get("prefer_thread", True))
        thread_id = anchor.get("thread_id") or ""
        chat_id = anchor.get("chat_id") or ""

        if prefer_thread and thread_id:
            container_id_type = "thread"
            container_id = thread_id
            result = _list_messages(
                container_id_type="thread",
                container_id=thread_id,
                sort_type="ByCreateTimeAsc",
                page_size=_page_size(args),
                card_msg_content_type=args.get("card_msg_content_type") or "",
            )
        else:
            if not chat_id:
                raise ValueError("message has no chat_id; cannot list nearby chat messages")

            create_sec = _to_seconds(anchor.get("create_time", ""))
            before_seconds = int(args.get("before_seconds", 3600))
            after_seconds = int(args.get("after_seconds", 0))
            start_time = str(max(0, create_sec - before_seconds)) if create_sec else None
            end_time = str(create_sec + after_seconds) if create_sec and after_seconds > 0 else None

            container_id_type = "chat"
            container_id = chat_id
            result = _list_messages(
                container_id_type="chat",
                container_id=chat_id,
                start_time=start_time,
                end_time=end_time,
                sort_type="ByCreateTimeAsc",
                page_size=_page_size(args),
                card_msg_content_type=args.get("card_msg_content_type") or "",
            )

        output = {
            "anchor_message": anchor,
            "container_id_type": container_id_type,
            "container_id": container_id,
            **result,
        }

        ctx.emit(
            "feishu.im.message_context.got",
            {
                "tool": "feishu_im_get_message_context",
                "message_id": message_id,
                "container_id_type": container_id_type,
                "container_id": container_id,
                "count": len(result["items"]),
            },
        )

        return output

    @registry.register(
        ToolSpec(
            name="feishu_im_reply_text",
            description="Reply to a Feishu IM message with text as bot.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                    "text": {"type": "string"},
                    "reply_in_thread": {"type": "boolean"},
                    "uuid": {"type": "string"},
                },
                "required": ["message_id", "text"],
                "additionalProperties": False,
            },
            output_schema=_message_id_schema(),
        )
    )
    def feishu_im_reply_text(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        message_id = args["message_id"]
        text = args["text"]

        body: dict[str, Any] = {
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        if "reply_in_thread" in args:
            body["reply_in_thread"] = bool(args.get("reply_in_thread"))
        if args.get("uuid"):
            body["uuid"] = args["uuid"]

        data = feishu_request(
            "POST",
            f"/open-apis/im/v1/messages/{message_id}/reply",
            body=body,
        )

        message = _normalize_message(data)
        result = {
            "message_id": message["message_id"],
            "root_id": message["root_id"],
            "parent_id": message["parent_id"],
            "thread_id": message["thread_id"],
            "chat_id": message["chat_id"],
            "msg_type": message["msg_type"],
            "create_time": message["create_time"],
        }

        ctx.emit(
            "feishu.im.message.replied",
            {
                "tool": "feishu_im_reply_text",
                "source_message_id": message_id,
                "message_id": result["message_id"],
                "chat_id": result["chat_id"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_reply_message",
            description="Reply to a Feishu IM message with a prepared message payload as bot.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                    "msg_type": {"type": "string"},
                    "content": {
                        "type": "object",
                        "description": "Message content object. It will be JSON-encoded.",
                    },
                    "reply_in_thread": {"type": "boolean"},
                    "uuid": {"type": "string"},
                },
                "required": ["message_id", "msg_type", "content"],
                "additionalProperties": False,
            },
            output_schema=_message_id_schema(),
        )
    )
    def feishu_im_reply_message(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        message_id = args["message_id"]
        msg_type = args["msg_type"]
        content = args["content"]

        body: dict[str, Any] = {
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        }
        if "reply_in_thread" in args:
            body["reply_in_thread"] = bool(args.get("reply_in_thread"))
        if args.get("uuid"):
            body["uuid"] = args["uuid"]

        data = feishu_request(
            "POST",
            f"/open-apis/im/v1/messages/{message_id}/reply",
            body=body,
        )

        message = _normalize_message(data)
        result = {
            "message_id": message["message_id"],
            "root_id": message["root_id"],
            "parent_id": message["parent_id"],
            "thread_id": message["thread_id"],
            "chat_id": message["chat_id"],
            "msg_type": message["msg_type"],
            "create_time": message["create_time"],
        }

        ctx.emit(
            "feishu.im.message.replied",
            {
                "tool": "feishu_im_reply_message",
                "source_message_id": message_id,
                "message_id": result["message_id"],
                "chat_id": result["chat_id"],
                "msg_type": result["msg_type"],
            },
        )

        return result
