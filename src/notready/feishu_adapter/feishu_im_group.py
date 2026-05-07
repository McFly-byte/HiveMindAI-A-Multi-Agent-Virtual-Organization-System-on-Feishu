from __future__ import annotations

import json
import uuid
from typing import Any

from tool_integration.events import EventBus
from tool_integration.tools import ToolRegistry, ToolSpec

from feishu_adapter.feishu_client import feishu_request


DEFAULT_USER_ID_TYPE = "user_id"
DEFAULT_MEMBER_ID_TYPE = "user_id"


def _first_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _items(data: dict[str, Any]) -> list[dict[str, Any]]:
    value = data.get("items") or data.get("chats") or data.get("members") or []
    return value if isinstance(value, list) else []


def _str_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _next_page_token(data: dict[str, Any]) -> str:
    return _first_str(data, "page_token", "next_page_token")


def _bool_query(value: bool | None) -> str | None:
    if value is None:
        return None
    return "true" if value else "false"


def _clean_body(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if v is not None and v != ""}


def _text_content(text: str) -> str:
    return json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":"))


def _message_result(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_id": _first_str(data, "message_id"),
        "chat_id": _first_str(data, "chat_id"),
        "msg_type": _first_str(data, "msg_type"),
        "create_time": _first_str(data, "create_time"),
    }


def _group_result(data: dict[str, Any], fallback_chat_id: str = "") -> dict[str, Any]:
    return {
        "chat_id": _first_str(data, "chat_id") or fallback_chat_id,
        "name": _first_str(data, "name"),
        "description": _first_str(data, "description"),
        "owner_id": _first_str(data, "owner_id"),
        "chat_mode": _first_str(data, "chat_mode"),
        "chat_type": _first_str(data, "chat_type"),
        "avatar": _first_str(data, "avatar"),
        "external": bool(data.get("external", False)),
    }


def register(registry: ToolRegistry, event_bus: EventBus | None = None):
    @registry.register(
        ToolSpec(
            name="feishu_im_send_text_to_user",
            description="Send a text message to a Feishu user by user_id.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "text": {"type": "string"},
                    "uuid": {"type": "string"},
                },
                "required": ["user_id", "text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                    "chat_id": {"type": "string"},
                    "msg_type": {"type": "string"},
                    "create_time": {"type": "string"},
                },
                "required": ["message_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_send_text_to_user(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        user_id = args["user_id"]
        text = args["text"]

        data = feishu_request(
            "POST",
            "/open-apis/im/v1/messages",
            queries={"receive_id_type": DEFAULT_USER_ID_TYPE},
            body={
                "receive_id": user_id,
                "msg_type": "text",
                "content": _text_content(text),
                "uuid": args.get("uuid") or str(uuid.uuid4()),
            },
        )

        result = _message_result(data)

        ctx.emit(
            "feishu.im.message.sent_to_user",
            {
                "tool": "feishu_im_send_text_to_user",
                "user_id": user_id,
                "message_id": result["message_id"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_send_text_to_chat",
            description="Send a text message to a Feishu chat by chat_id.",
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
                    "message_id": {"type": "string"},
                    "chat_id": {"type": "string"},
                    "msg_type": {"type": "string"},
                    "create_time": {"type": "string"},
                },
                "required": ["message_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_send_text_to_chat(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        chat_id = args["chat_id"]
        text = args["text"]

        data = feishu_request(
            "POST",
            "/open-apis/im/v1/messages",
            queries={"receive_id_type": "chat_id"},
            body={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": _text_content(text),
                "uuid": args.get("uuid") or str(uuid.uuid4()),
            },
        )

        result = _message_result(data)

        ctx.emit(
            "feishu.im.message.sent_to_chat",
            {
                "tool": "feishu_im_send_text_to_chat",
                "chat_id": chat_id,
                "message_id": result["message_id"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_create_group",
            description="Create a Feishu group chat.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "owner_id": {"type": "string"},
                    "chat_type": {"type": "string"},
                    "external": {"type": "boolean"},
                    "set_bot_manager": {"type": "boolean"},
                    "uuid": {"type": "string"},
                    "join_message_visibility": {"type": "string"},
                    "leave_message_visibility": {"type": "string"},
                    "membership_approval": {"type": "string"},
                    "add_member_permission": {"type": "string"},
                    "share_card_permission": {"type": "string"},
                    "at_all_permission": {"type": "string"},
                    "edit_permission": {"type": "string"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "owner_id": {"type": "string"},
                    "chat_mode": {"type": "string"},
                    "chat_type": {"type": "string"},
                    "avatar": {"type": "string"},
                    "external": {"type": "boolean"},
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_create_group(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        name = args["name"]

        data = feishu_request(
            "POST",
            "/open-apis/im/v1/chats",
            queries={
                "user_id_type": DEFAULT_USER_ID_TYPE,
                "set_bot_manager": _bool_query(args.get("set_bot_manager")),
                "uuid": args.get("uuid"),
            },
            body=_clean_body(
                {
                    "name": name,
                    "description": args.get("description"),
                    "owner_id": args.get("owner_id"),
                    "chat_mode": "group",
                    "chat_type": args.get("chat_type") or "private",
                    "external": args.get("external", False),
                    "join_message_visibility": args.get("join_message_visibility"),
                    "leave_message_visibility": args.get("leave_message_visibility"),
                    "membership_approval": args.get("membership_approval"),
                    "add_member_permission": args.get("add_member_permission"),
                    "share_card_permission": args.get("share_card_permission"),
                    "at_all_permission": args.get("at_all_permission"),
                    "edit_permission": args.get("edit_permission"),
                }
            ),
        )

        result = _group_result(data)

        ctx.emit(
            "feishu.im.group.created",
            {
                "tool": "feishu_im_create_group",
                "chat_id": result["chat_id"],
                "name": result["name"] or name,
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_create_group_with_members",
            description="Create a Feishu group, add members, and optionally send a welcome message.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "owner_id": {"type": "string"},
                    "user_ids": {"type": "array", "items": {"type": "string"}},
                    "welcome_text": {"type": "string"},
                    "set_bot_manager": {"type": "boolean"},
                    "uuid": {"type": "string"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "name": {"type": "string"},
                    "member_count": {"type": "integer"},
                    "invalid_id_list": {"type": "array", "items": {"type": "string"}},
                    "welcome_message_id": {"type": "string"},
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_create_group_with_members(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        user_ids = [x for x in args.get("user_ids", []) if isinstance(x, str) and x]
        welcome_text = args.get("welcome_text", "")

        group_data = feishu_request(
            "POST",
            "/open-apis/im/v1/chats",
            queries={
                "user_id_type": DEFAULT_USER_ID_TYPE,
                "set_bot_manager": _bool_query(args.get("set_bot_manager", True)),
                "uuid": args.get("uuid"),
            },
            body=_clean_body(
                {
                    "name": args["name"],
                    "description": args.get("description"),
                    "owner_id": args.get("owner_id"),
                    "chat_mode": "group",
                    "chat_type": "private",
                    "external": False,
                    "join_message_visibility": "all_members",
                    "leave_message_visibility": "all_members",
                    "membership_approval": "no_approval_required",
                }
            ),
        )

        group = _group_result(group_data)
        chat_id = group["chat_id"]
        invalid_id_list: list[str] = []
        welcome_message_id = ""

        if user_ids:
            member_data = feishu_request(
                "POST",
                f"/open-apis/im/v1/chats/{chat_id}/members",
                queries={
                    "member_id_type": DEFAULT_MEMBER_ID_TYPE,
                    "succeed_type": args.get("succeed_type"),
                },
                body={"id_list": user_ids},
            )
            invalid_id_list = _str_list(member_data, "invalid_id_list")

        if welcome_text:
            message_data = feishu_request(
                "POST",
                "/open-apis/im/v1/messages",
                queries={"receive_id_type": "chat_id"},
                body={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": _text_content(welcome_text),
                    "uuid": str(uuid.uuid4()),
                },
            )
            welcome_message_id = _first_str(message_data, "message_id")

        result = {
            "chat_id": chat_id,
            "name": group["name"] or args["name"],
            "member_count": len(user_ids) - len(invalid_id_list),
            "invalid_id_list": invalid_id_list,
            "welcome_message_id": welcome_message_id,
        }

        ctx.emit(
            "feishu.im.group.created_with_members",
            {
                "tool": "feishu_im_create_group_with_members",
                "chat_id": chat_id,
                "member_count": result["member_count"],
                "invalid_count": len(invalid_id_list),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_get_group",
            description="Get Feishu group chat metadata.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "owner_id": {"type": "string"},
                    "chat_mode": {"type": "string"},
                    "chat_type": {"type": "string"},
                    "avatar": {"type": "string"},
                    "external": {"type": "boolean"},
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_get_group(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        chat_id = args["chat_id"]

        data = feishu_request(
            "GET",
            f"/open-apis/im/v1/chats/{chat_id}",
            queries={"user_id_type": DEFAULT_USER_ID_TYPE},
        )

        result = _group_result(data, fallback_chat_id=chat_id)

        ctx.emit(
            "feishu.im.group.got",
            {
                "tool": "feishu_im_get_group",
                "chat_id": chat_id,
                "name": result["name"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_update_group_info",
            description="Update Feishu group name, description, owner, or common permissions.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "owner_id": {"type": "string"},
                    "avatar": {"type": "string"},
                    "add_member_permission": {"type": "string"},
                    "share_card_permission": {"type": "string"},
                    "at_all_permission": {"type": "string"},
                    "edit_permission": {"type": "string"},
                    "join_message_visibility": {"type": "string"},
                    "leave_message_visibility": {"type": "string"},
                    "membership_approval": {"type": "string"},
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "owner_id": {"type": "string"},
                    "updated": {"type": "boolean"},
                },
                "required": ["chat_id", "updated"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_update_group_info(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        chat_id = args["chat_id"]

        body = _clean_body(
            {
                "name": args.get("name"),
                "description": args.get("description"),
                "owner_id": args.get("owner_id"),
                "avatar": args.get("avatar"),
                "add_member_permission": args.get("add_member_permission"),
                "share_card_permission": args.get("share_card_permission"),
                "at_all_permission": args.get("at_all_permission"),
                "edit_permission": args.get("edit_permission"),
                "join_message_visibility": args.get("join_message_visibility"),
                "leave_message_visibility": args.get("leave_message_visibility"),
                "membership_approval": args.get("membership_approval"),
            }
        )

        if not body:
            raise ValueError("at least one group info field is required")

        data = feishu_request(
            "PUT",
            f"/open-apis/im/v1/chats/{chat_id}",
            queries={"user_id_type": DEFAULT_USER_ID_TYPE},
            body=body,
        )

        result = {
            "chat_id": _first_str(data, "chat_id") or chat_id,
            "name": _first_str(data, "name") or args.get("name", ""),
            "description": _first_str(data, "description") or args.get("description", ""),
            "owner_id": _first_str(data, "owner_id") or args.get("owner_id", ""),
            "updated": True,
        }

        ctx.emit(
            "feishu.im.group.updated",
            {
                "tool": "feishu_im_update_group_info",
                "chat_id": chat_id,
                "fields": list(body.keys()),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_delete_group",
            description="Dissolve a Feishu group chat.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "deleted": {"type": "boolean"},
                },
                "required": ["chat_id", "deleted"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_delete_group(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        chat_id = args["chat_id"]

        feishu_request(
            "DELETE",
            f"/open-apis/im/v1/chats/{chat_id}",
        )

        result = {
            "chat_id": chat_id,
            "deleted": True,
        }

        ctx.emit(
            "feishu.im.group.deleted",
            {
                "tool": "feishu_im_delete_group",
                "chat_id": chat_id,
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_list_groups",
            description="List groups visible to the current Feishu bot.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                    "sort_type": {"type": "string"},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                    "has_more": {"type": "boolean"},
                    "page_token": {"type": "string"},
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_list_groups(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        data = feishu_request(
            "GET",
            "/open-apis/im/v1/chats",
            queries={
                "user_id_type": DEFAULT_USER_ID_TYPE,
                "page_size": args.get("page_size", 20),
                "page_token": args.get("page_token"),
                "sort_type": args.get("sort_type"),
            },
        )

        items = _items(data)
        result = {
            "items": items,
            "has_more": bool(data.get("has_more", False)),
            "page_token": _next_page_token(data),
        }

        ctx.emit(
            "feishu.im.groups.listed",
            {
                "tool": "feishu_im_list_groups",
                "count": len(items),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_search_groups",
            description="Search groups visible to the current Feishu bot.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                    "has_more": {"type": "boolean"},
                    "page_token": {"type": "string"},
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_search_groups(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        query = args["query"]

        data = feishu_request(
            "GET",
            "/open-apis/im/v1/chats/search",
            queries={
                "user_id_type": DEFAULT_USER_ID_TYPE,
                "query": query,
                "page_size": args.get("page_size", 20),
                "page_token": args.get("page_token"),
            },
        )

        items = _items(data)
        result = {
            "items": items,
            "has_more": bool(data.get("has_more", False)),
            "page_token": _next_page_token(data),
        }

        ctx.emit(
            "feishu.im.groups.searched",
            {
                "tool": "feishu_im_search_groups",
                "query": query,
                "count": len(items),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_add_group_members",
            description="Add users or bots to a Feishu group chat.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "member_ids": {"type": "array", "items": {"type": "string"}},
                    "succeed_type": {"type": "integer"},
                },
                "required": ["chat_id", "member_ids"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "invalid_id_list": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["chat_id", "invalid_id_list"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_add_group_members(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        chat_id = args["chat_id"]
        member_ids = [x for x in args.get("member_ids", []) if isinstance(x, str) and x]
        if not member_ids:
            raise ValueError("member_ids must not be empty")

        data = feishu_request(
            "POST",
            f"/open-apis/im/v1/chats/{chat_id}/members",
            queries={
                "member_id_type": DEFAULT_MEMBER_ID_TYPE,
                "succeed_type": args.get("succeed_type"),
            },
            body={"id_list": member_ids},
        )

        result = {
            "chat_id": chat_id,
            "invalid_id_list": _str_list(data, "invalid_id_list"),
        }

        ctx.emit(
            "feishu.im.group.members.added",
            {
                "tool": "feishu_im_add_group_members",
                "chat_id": chat_id,
                "member_count": len(member_ids),
                "invalid_count": len(result["invalid_id_list"]),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_remove_group_members",
            description="Remove users or bots from a Feishu group chat.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "member_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["chat_id", "member_ids"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "invalid_id_list": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["chat_id", "invalid_id_list"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_remove_group_members(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        chat_id = args["chat_id"]
        member_ids = [x for x in args.get("member_ids", []) if isinstance(x, str) and x]
        if not member_ids:
            raise ValueError("member_ids must not be empty")

        data = feishu_request(
            "DELETE",
            f"/open-apis/im/v1/chats/{chat_id}/members",
            queries={"member_id_type": DEFAULT_MEMBER_ID_TYPE},
            body={"id_list": member_ids},
        )

        result = {
            "chat_id": chat_id,
            "invalid_id_list": _str_list(data, "invalid_id_list"),
        }

        ctx.emit(
            "feishu.im.group.members.removed",
            {
                "tool": "feishu_im_remove_group_members",
                "chat_id": chat_id,
                "member_count": len(member_ids),
                "invalid_count": len(result["invalid_id_list"]),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_list_group_members",
            description="List Feishu group members.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                    "has_more": {"type": "boolean"},
                    "page_token": {"type": "string"},
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_list_group_members(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        chat_id = args["chat_id"]

        data = feishu_request(
            "GET",
            f"/open-apis/im/v1/chats/{chat_id}/members",
            queries={
                "member_id_type": DEFAULT_MEMBER_ID_TYPE,
                "page_size": args.get("page_size", 50),
                "page_token": args.get("page_token"),
            },
        )

        items = _items(data)
        result = {
            "items": items,
            "has_more": bool(data.get("has_more", False)),
            "page_token": _next_page_token(data),
        }

        ctx.emit(
            "feishu.im.group.members.listed",
            {
                "tool": "feishu_im_list_group_members",
                "chat_id": chat_id,
                "count": len(items),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_is_in_group",
            description="Check whether the current Feishu bot is in a group chat.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "is_in_chat": {"type": "boolean"},
                },
                "required": ["chat_id", "is_in_chat"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_is_in_group(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        chat_id = args["chat_id"]

        data = feishu_request(
            "GET",
            f"/open-apis/im/v1/chats/{chat_id}/members/is_in_chat",
        )

        result = {
            "chat_id": chat_id,
            "is_in_chat": bool(data.get("is_in_chat", False)),
        }

        ctx.emit(
            "feishu.im.group.membership.checked",
            {
                "tool": "feishu_im_is_in_group",
                "chat_id": chat_id,
                "is_in_chat": result["is_in_chat"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_add_group_managers",
            description="Add Feishu group administrators.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "manager_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["chat_id", "manager_ids"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "chat_managers": {"type": "array", "items": {"type": "string"}},
                    "chat_bot_managers": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_add_group_managers(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        chat_id = args["chat_id"]
        manager_ids = [x for x in args.get("manager_ids", []) if isinstance(x, str) and x]
        if not manager_ids:
            raise ValueError("manager_ids must not be empty")

        data = feishu_request(
            "POST",
            f"/open-apis/im/v1/chats/{chat_id}/managers/add_managers",
            body={"manager_ids": manager_ids},
        )

        result = {
            "chat_id": chat_id,
            "chat_managers": _str_list(data, "chat_managers"),
            "chat_bot_managers": _str_list(data, "chat_bot_managers"),
        }

        ctx.emit(
            "feishu.im.group.managers.added",
            {
                "tool": "feishu_im_add_group_managers",
                "chat_id": chat_id,
                "manager_count": len(manager_ids),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_delete_group_managers",
            description="Delete Feishu group administrators.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "manager_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["chat_id", "manager_ids"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "chat_managers": {"type": "array", "items": {"type": "string"}},
                    "chat_bot_managers": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_delete_group_managers(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        chat_id = args["chat_id"]
        manager_ids = [x for x in args.get("manager_ids", []) if isinstance(x, str) and x]
        if not manager_ids:
            raise ValueError("manager_ids must not be empty")

        data = feishu_request(
            "GET",
            f"/open-apis/im/v1/chats/{chat_id}/managers/delete_managers",
            body={"manager_ids": manager_ids},
        )

        result = {
            "chat_id": chat_id,
            "chat_managers": _str_list(data, "chat_managers"),
            "chat_bot_managers": _str_list(data, "chat_bot_managers"),
        }

        ctx.emit(
            "feishu.im.group.managers.deleted",
            {
                "tool": "feishu_im_delete_group_managers",
                "chat_id": chat_id,
                "manager_count": len(manager_ids),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_im_create_group_share_link",
            description="Create a share link for a Feishu group chat.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "validity_period": {"type": "string"},
                },
                "required": ["chat_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string"},
                    "share_link": {"type": "string"},
                    "expire_time": {"type": "string"},
                    "is_permanent": {"type": "boolean"},
                },
                "required": ["chat_id", "share_link"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_im_create_group_share_link(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        chat_id = args["chat_id"]

        data = feishu_request(
            "POST",
            f"/open-apis/im/v1/chats/{chat_id}/link",
            body=_clean_body(
                {
                    "validity_period": args.get("validity_period") or "week",
                }
            ),
        )

        result = {
            "chat_id": chat_id,
            "share_link": _first_str(data, "share_link"),
            "expire_time": _first_str(data, "expire_time"),
            "is_permanent": bool(data.get("is_permanent", False)),
        }

        ctx.emit(
            "feishu.im.group.share_link.created",
            {
                "tool": "feishu_im_create_group_share_link",
                "chat_id": chat_id,
                "is_permanent": result["is_permanent"],
            },
        )

        return result
