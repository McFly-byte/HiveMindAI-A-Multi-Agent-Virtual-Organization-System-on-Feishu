from __future__ import annotations

from typing import Any

from tool_integration.events import EventBus
from tool_integration.tools import ToolRegistry, ToolSpec

from feishu_adapter.feishu_client import feishu_request


OBJ_TYPES = ["doc", "docx", "sheet", "bitable", "mindnote", "file", "slides"]
NODE_TYPES = ["origin", "shortcut"]


def _s(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _b(data: dict[str, Any], key: str) -> bool:
    return bool(data.get(key, False))


def _space(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("space") if isinstance(data.get("space"), dict) else data
    return {
        "space_id": _s(raw, "space_id"),
        "name": _s(raw, "name"),
        "description": _s(raw, "description"),
    }


def _node(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("node") if isinstance(data.get("node"), dict) else data

    return {
        "space_id": _s(raw, "space_id"),
        "node_token": _s(raw, "node_token"),
        "obj_token": _s(raw, "obj_token"),
        "obj_type": _s(raw, "obj_type"),
        "parent_node_token": _s(raw, "parent_node_token"),
        "node_type": _s(raw, "node_type"),
        "origin_node_token": _s(raw, "origin_node_token"),
        "origin_space_id": _s(raw, "origin_space_id"),
        "has_child": _b(raw, "has_child"),
        "title": _s(raw, "title"),
    }


def _space_page(data: dict[str, Any]) -> dict[str, Any]:
    items = data.get("items") or []
    if not isinstance(items, list):
        items = []

    return {
        "items": [_space(item) for item in items if isinstance(item, dict)],
        "has_more": bool(data.get("has_more", False)),
        "page_token": data.get("page_token") or data.get("next_page_token") or "",
    }


def _node_page(data: dict[str, Any]) -> dict[str, Any]:
    items = data.get("items") or []
    if not isinstance(items, list):
        items = []

    return {
        "items": [_node(item) for item in items if isinstance(item, dict)],
        "has_more": bool(data.get("has_more", False)),
        "page_token": data.get("page_token") or data.get("next_page_token") or "",
    }


def _node_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "space_id": {"type": "string"},
            "node_token": {"type": "string"},
            "obj_token": {"type": "string"},
            "obj_type": {"type": "string"},
            "parent_node_token": {"type": "string"},
            "node_type": {"type": "string"},
            "origin_node_token": {"type": "string"},
            "origin_space_id": {"type": "string"},
            "has_child": {"type": "boolean"},
            "title": {"type": "string"},
        },
        "required": ["node_token"],
        "additionalProperties": False,
    }


def register(registry: ToolRegistry, event_bus: EventBus | None = None):
    @registry.register(
        ToolSpec(
            name="feishu_wiki_list_spaces",
            description="List accessible Feishu Wiki spaces.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                },
                "required": [],
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
    def feishu_wiki_list_spaces(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        data = feishu_request(
            "GET",
            "/open-apis/wiki/v2/spaces",
            queries={
                "page_size": args.get("page_size", 20),
                "page_token": args.get("page_token"),
            },
        )

        result = _space_page(data)

        ctx.emit(
            "feishu.wiki.spaces.listed",
            {
                "tool": "feishu_wiki_list_spaces",
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_wiki_get_space",
            description="Get Feishu Wiki space info.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "space_id": {"type": "string"},
                },
                "required": ["space_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "space_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["space_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_wiki_get_space(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        space_id = args["space_id"]

        data = feishu_request(
            "GET",
            f"/open-apis/wiki/v2/spaces/{space_id}",
        )

        result = _space(data)
        if not result["space_id"]:
            result["space_id"] = space_id

        ctx.emit(
            "feishu.wiki.space.got",
            {
                "tool": "feishu_wiki_get_space",
                "space_id": result["space_id"],
                "name": result["name"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_wiki_list_nodes",
            description="List child nodes in a Feishu Wiki space.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "space_id": {"type": "string"},
                    "parent_node_token": {
                        "type": "string",
                        "description": "Optional parent wiki node token. Empty means root nodes.",
                    },
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                },
                "required": ["space_id"],
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
    def feishu_wiki_list_nodes(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        space_id = args["space_id"]

        data = feishu_request(
            "GET",
            f"/open-apis/wiki/v2/spaces/{space_id}/nodes",
            queries={
                "parent_node_token": args.get("parent_node_token"),
                "page_size": args.get("page_size", 20),
                "page_token": args.get("page_token"),
            },
        )

        result = _node_page(data)

        ctx.emit(
            "feishu.wiki.nodes.listed",
            {
                "tool": "feishu_wiki_list_nodes",
                "space_id": space_id,
                "parent_node_token": args.get("parent_node_token", ""),
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_wiki_get_node",
            description="Get Feishu Wiki node info by node_token.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "node_token": {"type": "string"},
                },
                "required": ["node_token"],
                "additionalProperties": False,
            },
            output_schema=_node_schema(),
        )
    )
    def feishu_wiki_get_node(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        node_token = args["node_token"]

        data = feishu_request(
            "GET",
            "/open-apis/wiki/v2/spaces/get_node",
            queries={"token": node_token},
        )

        result = _node(data)

        ctx.emit(
            "feishu.wiki.node.got",
            {
                "tool": "feishu_wiki_get_node",
                "space_id": result["space_id"],
                "node_token": result["node_token"],
                "obj_token": result["obj_token"],
                "obj_type": result["obj_type"],
                "title": result["title"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_wiki_create_node",
            description="Create a Feishu Wiki node.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "space_id": {"type": "string"},
                    "title": {"type": "string"},
                    "obj_type": {
                        "type": "string",
                        "enum": OBJ_TYPES,
                        "description": "Default is docx.",
                    },
                    "parent_node_token": {
                        "type": "string",
                        "description": "Optional parent node token. Empty means root.",
                    },
                    "node_type": {
                        "type": "string",
                        "enum": NODE_TYPES,
                        "description": "Default is origin. Use shortcut with origin_node_token.",
                    },
                    "origin_node_token": {
                        "type": "string",
                        "description": "Required when node_type is shortcut.",
                    },
                },
                "required": ["space_id", "title"],
                "additionalProperties": False,
            },
            output_schema=_node_schema(),
        )
    )
    def feishu_wiki_create_node(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        space_id = args["space_id"]
        node_type = args.get("node_type") or "origin"
        obj_type = args.get("obj_type") or "docx"

        if node_type == "shortcut" and not args.get("origin_node_token"):
            raise ValueError("origin_node_token is required when node_type is shortcut")

        body: dict[str, Any] = {
            "title": args["title"],
            "obj_type": obj_type,
            "node_type": node_type,
        }

        if args.get("parent_node_token"):
            body["parent_node_token"] = args["parent_node_token"]

        if args.get("origin_node_token"):
            body["origin_node_token"] = args["origin_node_token"]

        data = feishu_request(
            "POST",
            f"/open-apis/wiki/v2/spaces/{space_id}/nodes",
            body=body,
        )

        result = _node(data)

        ctx.emit(
            "feishu.wiki.node.created",
            {
                "tool": "feishu_wiki_create_node",
                "space_id": space_id,
                "node_token": result["node_token"],
                "obj_token": result["obj_token"],
                "obj_type": result["obj_type"],
                "title": result["title"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_wiki_move_node",
            description="Move a Feishu Wiki node inside Wiki. Supports cross-space move.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "space_id": {
                        "type": "string",
                        "description": "Source space id.",
                    },
                    "node_token": {
                        "type": "string",
                        "description": "Source node token.",
                    },
                    "target_parent_token": {
                        "type": "string",
                        "description": "Target parent node token. Empty means target space root.",
                    },
                    "target_space_id": {
                        "type": "string",
                        "description": "Optional target space id. Empty means same space.",
                    },
                },
                "required": ["space_id", "node_token"],
                "additionalProperties": False,
            },
            output_schema=_node_schema(),
        )
    )
    def feishu_wiki_move_node(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        space_id = args["space_id"]
        node_token = args["node_token"]

        body: dict[str, Any] = {}

        if args.get("target_parent_token"):
            body["target_parent_token"] = args["target_parent_token"]

        if args.get("target_space_id"):
            body["target_space_id"] = args["target_space_id"]

        if not body:
            raise ValueError("target_parent_token or target_space_id is required")

        data = feishu_request(
            "POST",
            f"/open-apis/wiki/v2/spaces/{space_id}/nodes/{node_token}/move",
            body=body,
        )

        result = _node(data)
        if not result["node_token"]:
            result["node_token"] = node_token
        if not result["space_id"]:
            result["space_id"] = args.get("target_space_id") or space_id

        ctx.emit(
            "feishu.wiki.node.moved",
            {
                "tool": "feishu_wiki_move_node",
                "source_space_id": space_id,
                "target_space_id": args.get("target_space_id", ""),
                "node_token": node_token,
                "target_parent_token": args.get("target_parent_token", ""),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_wiki_copy_node",
            description="Copy a Feishu Wiki node to a target location.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "space_id": {
                        "type": "string",
                        "description": "Source space id.",
                    },
                    "node_token": {
                        "type": "string",
                        "description": "Source node token.",
                    },
                    "target_parent_token": {
                        "type": "string",
                        "description": "Target parent node token.",
                    },
                    "target_space_id": {
                        "type": "string",
                        "description": "Target space id.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional copied node title. Omit to keep original title.",
                    },
                },
                "required": ["space_id", "node_token"],
                "additionalProperties": False,
            },
            output_schema=_node_schema(),
        )
    )
    def feishu_wiki_copy_node(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        space_id = args["space_id"]
        node_token = args["node_token"]

        body: dict[str, Any] = {}

        if args.get("target_parent_token"):
            body["target_parent_token"] = args["target_parent_token"]

        if args.get("target_space_id"):
            body["target_space_id"] = args["target_space_id"]

        if "title" in args:
            body["title"] = args.get("title") or ""

        if not body.get("target_parent_token") and not body.get("target_space_id"):
            raise ValueError("target_parent_token or target_space_id is required")

        data = feishu_request(
            "POST",
            f"/open-apis/wiki/v2/spaces/{space_id}/nodes/{node_token}/copy",
            body=body,
        )

        result = _node(data)

        ctx.emit(
            "feishu.wiki.node.copied",
            {
                "tool": "feishu_wiki_copy_node",
                "source_space_id": space_id,
                "target_space_id": args.get("target_space_id", ""),
                "source_node_token": node_token,
                "new_node_token": result["node_token"],
                "target_parent_token": args.get("target_parent_token", ""),
                "title": result["title"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_wiki_update_node_title",
            description="Update Feishu Wiki node title.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "space_id": {"type": "string"},
                    "node_token": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["space_id", "node_token", "title"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "node_token": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["node_token", "title"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_wiki_update_node_title(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        space_id = args["space_id"]
        node_token = args["node_token"]
        title = args["title"]

        feishu_request(
            "POST",
            f"/open-apis/wiki/v2/spaces/{space_id}/nodes/{node_token}/update_title",
            body={"title": title},
        )

        ctx.emit(
            "feishu.wiki.node_title.updated",
            {
                "tool": "feishu_wiki_update_node_title",
                "space_id": space_id,
                "node_token": node_token,
                "title": title,
            },
        )

        return {
            "node_token": node_token,
            "title": title,
        }

    @registry.register(
        ToolSpec(
            name="feishu_wiki_move_drive_doc_to_wiki",
            description="Move an existing Drive document into a Feishu Wiki space.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "space_id": {"type": "string"},
                    "obj_token": {
                        "type": "string",
                        "description": "Drive document token.",
                    },
                    "obj_type": {
                        "type": "string",
                        "enum": OBJ_TYPES,
                    },
                    "parent_wiki_token": {
                        "type": "string",
                        "description": "Target parent wiki node token. Empty means root.",
                    },
                    "apply": {
                        "type": "boolean",
                        "description": "Optional confirmation/apply behavior.",
                    },
                },
                "required": ["space_id", "obj_token", "obj_type"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "wiki_token": {"type": "string"},
                    "node_token": {"type": "string"},
                    "task_id": {"type": "string"},
                },
                "required": [],
                "additionalProperties": False,
            },
        )
    )
    def feishu_wiki_move_drive_doc_to_wiki(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        space_id = args["space_id"]

        body: dict[str, Any] = {
            "obj_token": args["obj_token"],
            "obj_type": args["obj_type"],
        }

        if args.get("parent_wiki_token"):
            body["parent_wiki_token"] = args["parent_wiki_token"]

        if "apply" in args:
            body["apply"] = bool(args["apply"])

        data = feishu_request(
            "POST",
            f"/open-apis/wiki/v2/spaces/{space_id}/nodes/move_docs_to_wiki",
            body=body,
        )

        result = {
            "wiki_token": data.get("wiki_token") or "",
            "node_token": data.get("node_token") or data.get("wiki_token") or "",
            "task_id": data.get("task_id") or "",
        }

        ctx.emit(
            "feishu.wiki.drive_doc_move.started",
            {
                "tool": "feishu_wiki_move_drive_doc_to_wiki",
                "space_id": space_id,
                "obj_token": args["obj_token"],
                "obj_type": args["obj_type"],
                "parent_wiki_token": args.get("parent_wiki_token", ""),
                "wiki_token": result["wiki_token"],
                "task_id": result["task_id"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_wiki_get_task",
            description="Get Feishu Wiki async task result.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "task_type": {
                        "type": "string",
                        "description": "Default is move.",
                    },
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "integer"},
                    "status_msg": {"type": "string"},
                    "wiki_token": {"type": "string"},
                    "node_token": {"type": "string"},
                },
                "required": [],
                "additionalProperties": False,
            },
        )
    )
    def feishu_wiki_get_task(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        task_id = args["task_id"]

        data = feishu_request(
            "GET",
            f"/open-apis/wiki/v2/tasks/{task_id}",
            queries={
                "task_type": args.get("task_type") or "move",
            },
        )

        raw_status = data.get("status", 0)
        try:
            status = int(raw_status)
        except Exception:
            status = 0

        result = {
            "status": status,
            "status_msg": data.get("status_msg") or "",
            "wiki_token": "",
            "node_token": "",
        }

        task_result = data.get("result") or data.get("results") or []

        if isinstance(task_result, list) and task_result:
            first = task_result[0]
            if isinstance(first, dict):
                result["wiki_token"] = first.get("wiki_token") or first.get("node_token") or ""
                result["node_token"] = first.get("node_token") or first.get("wiki_token") or ""
        elif isinstance(task_result, dict):
            result["wiki_token"] = task_result.get("wiki_token") or task_result.get("node_token") or ""
            result["node_token"] = task_result.get("node_token") or task_result.get("wiki_token") or ""

        if not result["wiki_token"]:
            result["wiki_token"] = data.get("wiki_token") or data.get("node_token") or ""

        if not result["node_token"]:
            result["node_token"] = data.get("node_token") or data.get("wiki_token") or ""

        ctx.emit(
            "feishu.wiki.task.got",
            {
                "tool": "feishu_wiki_get_task",
                "task_id": task_id,
                "status": result["status"],
                "status_msg": result["status_msg"],
                "wiki_token": result["wiki_token"],
                "node_token": result["node_token"],
            },
        )

        return result