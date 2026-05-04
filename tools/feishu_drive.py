from __future__ import annotations

from typing import Any

from tooltest.tools import ToolRegistry, ToolSpec
from tooltest.events import EventBus

from tools.feishu_client import feishu_request


def _first_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _items(data: dict[str, Any]) -> list[dict[str, Any]]:
    value = (
        data.get("items")
        or data.get("files")
        or data.get("children")
        or data.get("file_list")
        or []
    )
    return value if isinstance(value, list) else []


def register(registry: ToolRegistry, event_bus: EventBus | None = None):
    @registry.register(
    ToolSpec(
        name="feishu_drive_list_folder",
        description="List items under a Feishu Drive folder.",
        mode="sync",
        kind="business",
        input_schema={
            "type": "object",
            "properties": {
                "folder_token": {"type": "string"},
                "page_size": {"type": "integer"},
                "page_token": {"type": "string"},
            },
            "required": ["folder_token"],
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
    def feishu_drive_list_folder(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        folder_token = args["folder_token"]

        data = feishu_request(
            "GET",
            "/open-apis/drive/v1/files",
            queries={
                "folder_token": folder_token,
                "page_size": args.get("page_size", 50),
                "page_token": args.get("page_token"),
            },
        )

        items = (
            data.get("files")
            or data.get("items")
            or data.get("file_list")
            or []
        )

        result = {
            "items": items,
            "has_more": bool(data.get("has_more", False)),
            "page_token": data.get("next_page_token") or data.get("page_token") or "",
        }

        ctx.emit(
            "feishu.drive.folder.listed",
            {
                "tool": "feishu_drive_list_folder",
                "folder_token": folder_token,
                "count": len(items),
                "has_more": result["has_more"],
            },
        )

        return result
    
    @registry.register(
        ToolSpec(
            name="feishu_drive_get_folder_meta",
            description="Get Feishu Drive folder metadata.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "folder_token": {"type": "string"},
                },
                "required": ["folder_token"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "token": {"type": "string"},
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "parent_token": {"type": "string"},
                    "url": {"type": "string"},
                    "user_id": {"type": "string"},
                },
                "required": ["token"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_drive_get_folder_meta(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        folder_token = args["folder_token"]

        data = feishu_request(
            "GET",
            f"/open-apis/drive/explorer/v2/folder/{folder_token}/meta",
        )

        result = {
            "token": _first_str(data, "token", "folder_token"),
            "id": _first_str(data, "id", "folder_id"),
            "name": _first_str(data, "name", "title"),
            "parent_token": _first_str(data, "parent_token"),
            "url": _first_str(data, "url"),
            "user_id": _first_str(data, "user_id", "owner_id"),
        }

        ctx.emit(
            "feishu.drive.folder_meta.got",
            {
                "tool": "feishu_drive_get_folder_meta",
                "folder_token": folder_token,
                "name": result["name"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_drive_create_folder",
            description="Create a Feishu Drive folder.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "folder_token": {
                        "type": "string",
                        "description": "Parent folder token. Empty means root if API allows it.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "token": {"type": "string"},
                    "name": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["token"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_drive_create_folder(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        name = args["name"]
        parent_token = args.get("folder_token", "")

        data = feishu_request(
            "POST",
            "/open-apis/drive/v1/files/create_folder",
            body={
                "name": name,
                "folder_token": parent_token,
            },
        )

        result = {
            "token": _first_str(data, "token", "folder_token"),
            "name": _first_str(data, "name") or name,
            "url": _first_str(data, "url"),
        }

        ctx.emit(
            "feishu.drive.folder.created",
            {
                "tool": "feishu_drive_create_folder",
                "token": result["token"],
                "name": result["name"],
                "parent_token": parent_token,
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_drive_move_or_delete_folder",
            description="Move or delete a Feishu Drive folder.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["move", "delete"],
                    },
                    "folder_token": {
                        "type": "string",
                        "description": "Source folder token.",
                    },
                    "target_folder_token": {
                        "type": "string",
                        "description": "Required when action is move.",
                    },
                },
                "required": ["action", "folder_token"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "folder_token": {"type": "string"},
                    "task_id": {"type": "string"},
                },
                "required": ["action", "folder_token"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_drive_move_or_delete_folder(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        action = args["action"]
        folder_token = args["folder_token"]

        if action == "move":
            target_folder_token = args.get("target_folder_token")
            if not target_folder_token:
                raise ValueError("target_folder_token is required when action is move")

            data = feishu_request(
                "POST",
                f"/open-apis/drive/v1/files/{folder_token}/move",
                queries={"type": "folder"},
                body={"folder_token": target_folder_token},
            )
        elif action == "delete":
            data = feishu_request(
                "DELETE",
                f"/open-apis/drive/v1/files/{folder_token}",
                queries={"type": "folder"},
            )
        else:
            raise ValueError(f"unsupported action: {action}")

        result = {
            "action": action,
            "folder_token": folder_token,
            "task_id": _first_str(data, "task_id"),
        }

        ctx.emit(
            "feishu.drive.folder.moved_or_deleted",
            {
                "tool": "feishu_drive_move_or_delete_folder",
                "action": action,
                "folder_token": folder_token,
                "task_id": result["task_id"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_drive_task_check",
            description="Check Feishu Drive async task status.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                },
                "required": ["status"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_drive_task_check(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        task_id = args["task_id"]

        data = feishu_request(
            "GET",
            "/open-apis/drive/v1/files/task_check",
            queries={"task_id": task_id},
        )

        result = {
            "status": _first_str(data, "status"),
        }

        ctx.emit(
            "feishu.drive.task.checked",
            {
                "tool": "feishu_drive_task_check",
                "task_id": task_id,
                "status": result["status"],
            },
        )

        return result