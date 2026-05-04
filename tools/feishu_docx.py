from __future__ import annotations

from typing import Any
from urllib.parse import quote

from tooltest.tools import ToolRegistry, ToolSpec
from tooltest.events import EventBus

from tools.feishu_client import feishu_request


DOCX_PREFIX = "/open-apis/docx/v1"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500

BLOCK_TYPE_PAGE = 1
BLOCK_TYPE_TEXT = 2


def _path(value: str) -> str:
    return quote(str(value), safe="")


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None and v != ""}


def _query(args: dict[str, Any], *keys: str) -> dict[str, Any]:
    return _clean({key: args.get(key) for key in keys})


def _page_size(args: dict[str, Any]) -> int:
    value = args.get("page_size", DEFAULT_PAGE_SIZE)
    try:
        value = int(value)
    except Exception:
        value = DEFAULT_PAGE_SIZE
    return max(1, min(value, MAX_PAGE_SIZE))


def _first_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _as_document(data: dict[str, Any]) -> dict[str, Any]:
    document = data.get("document") or {}
    return {
        "document_id": _first_str(document, "document_id"),
        "title": _first_str(document, "title"),
        "revision_id": document.get("revision_id"),
    }


def _as_page(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "items": data.get("items") or data.get("children") or [],
        "has_more": bool(data.get("has_more", False)),
        "page_token": data.get("page_token") or data.get("next_page_token") or "",
    }


def _text_element(
    content: str,
    text_element_style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "text_run": {
            "content": content,
            "text_element_style": text_element_style or {},
        }
    }


def _text_block(
    content: str,
    *,
    text_style: dict[str, Any] | None = None,
    text_element_style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "block_type": BLOCK_TYPE_TEXT,
        "text": {
            "elements": [_text_element(content, text_element_style)],
            "style": text_style or {},
        },
    }


def _list_blocks_once(
    document_id: str,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    page_token: str | None = None,
    document_revision_id: int | str | None = None,
    user_id_type: str | None = None,
    user_access_token: str | None = None,
) -> dict[str, Any]:
    return feishu_request(
        "GET",
        f"{DOCX_PREFIX}/documents/{_path(document_id)}/blocks",
        queries=_clean(
            {
                "page_size": page_size,
                "page_token": page_token,
                "document_revision_id": document_revision_id,
                "user_id_type": user_id_type,
            }
        ),
        user_access_token=user_access_token,
    )


def _find_root_block_id(
    document_id: str,
    *,
    document_revision_id: int | str | None = None,
    user_id_type: str | None = None,
    user_access_token: str | None = None,
) -> str:
    page_token: str | None = None

    for _ in range(20):
        data = _list_blocks_once(
            document_id,
            page_size=MAX_PAGE_SIZE,
            page_token=page_token,
            document_revision_id=document_revision_id,
            user_id_type=user_id_type,
            user_access_token=user_access_token,
        )

        for block in data.get("items") or []:
            if block.get("block_type") == BLOCK_TYPE_PAGE:
                return _first_str(block, "block_id") or document_id

            if block.get("parent_id") == "":
                return _first_str(block, "block_id") or document_id

        if not data.get("has_more"):
            break

        page_token = data.get("page_token") or data.get("next_page_token")
        if not page_token:
            break

    return document_id


def register(registry: ToolRegistry, event_bus: EventBus | None = None):
    @registry.register(
        ToolSpec(
            name="feishu_docx_create_document",
            description="Create a Feishu Docx document.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "folder_token": {
                        "type": "string",
                        "description": "Optional parent Drive folder token.",
                    },
                    "user_access_token": {"type": "string"},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "title": {"type": "string"},
                    "revision_id": {"type": "integer"},
                },
                "required": ["document_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_docx_create_document(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        data = feishu_request(
            "POST",
            f"{DOCX_PREFIX}/documents",
            body=_clean(
                {
                    "title": args.get("title"),
                    "folder_token": args.get("folder_token"),
                }
            ),
            user_access_token=args.get("user_access_token"),
        )

        result = _as_document(data)

        ctx.emit(
            "feishu.docx.document.created",
            {
                "tool": "feishu_docx_create_document",
                "document_id": result["document_id"],
                "title": result["title"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_docx_get_document",
            description="Get Feishu Docx document metadata.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "user_access_token": {"type": "string"},
                },
                "required": ["document_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "title": {"type": "string"},
                    "revision_id": {"type": "integer"},
                },
                "required": ["document_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_docx_get_document(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        document_id = args["document_id"]

        data = feishu_request(
            "GET",
            f"{DOCX_PREFIX}/documents/{_path(document_id)}",
            user_access_token=args.get("user_access_token"),
        )

        result = _as_document(data)

        ctx.emit(
            "feishu.docx.document.got",
            {
                "tool": "feishu_docx_get_document",
                "document_id": result["document_id"],
                "revision_id": result.get("revision_id"),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_docx_get_raw_content",
            description="Get plain text content from a Feishu Docx document.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "lang": {"type": "string"},
                    "user_access_token": {"type": "string"},
                },
                "required": ["document_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["document_id", "content"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_docx_get_raw_content(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        document_id = args["document_id"]

        data = feishu_request(
            "GET",
            f"{DOCX_PREFIX}/documents/{_path(document_id)}/raw_content",
            queries=_query(args, "lang"),
            user_access_token=args.get("user_access_token"),
        )

        result = {
            "document_id": document_id,
            "content": _first_str(data, "content"),
        }

        ctx.emit(
            "feishu.docx.raw_content.got",
            {
                "tool": "feishu_docx_get_raw_content",
                "document_id": document_id,
                "content_length": len(result["content"]),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_docx_list_blocks",
            description="List blocks in a Feishu Docx document.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                    "document_revision_id": {"type": ["integer", "string"]},
                    "user_id_type": {"type": "string"},
                    "user_access_token": {"type": "string"},
                },
                "required": ["document_id"],
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
    def feishu_docx_list_blocks(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        document_id = args["document_id"]

        data = feishu_request(
            "GET",
            f"{DOCX_PREFIX}/documents/{_path(document_id)}/blocks",
            queries=_clean(
                {
                    "page_size": _page_size(args),
                    "page_token": args.get("page_token"),
                    "document_revision_id": args.get("document_revision_id"),
                    "user_id_type": args.get("user_id_type"),
                }
            ),
            user_access_token=args.get("user_access_token"),
        )

        result = _as_page(data)

        ctx.emit(
            "feishu.docx.blocks.listed",
            {
                "tool": "feishu_docx_list_blocks",
                "document_id": document_id,
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_docx_get_block",
            description="Get one block from a Feishu Docx document.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "block_id": {"type": "string"},
                    "document_revision_id": {"type": ["integer", "string"]},
                    "user_id_type": {"type": "string"},
                    "user_access_token": {"type": "string"},
                },
                "required": ["document_id", "block_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "block": {"type": "object"},
                },
                "required": ["block"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_docx_get_block(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        document_id = args["document_id"]
        block_id = args["block_id"]

        data = feishu_request(
            "GET",
            f"{DOCX_PREFIX}/documents/{_path(document_id)}/blocks/{_path(block_id)}",
            queries=_query(args, "document_revision_id", "user_id_type"),
            user_access_token=args.get("user_access_token"),
        )

        block = data.get("block") or {}

        ctx.emit(
            "feishu.docx.block.got",
            {
                "tool": "feishu_docx_get_block",
                "document_id": document_id,
                "block_id": _first_str(block, "block_id") or block_id,
                "block_type": block.get("block_type"),
            },
        )

        return {"block": block}

    @registry.register(
        ToolSpec(
            name="feishu_docx_get_root_block",
            description="Find root block id of a Feishu Docx document.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "document_revision_id": {"type": ["integer", "string"]},
                    "user_id_type": {"type": "string"},
                    "user_access_token": {"type": "string"},
                },
                "required": ["document_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "root_block_id": {"type": "string"},
                },
                "required": ["document_id", "root_block_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_docx_get_root_block(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        document_id = args["document_id"]

        root_block_id = _find_root_block_id(
            document_id,
            document_revision_id=args.get("document_revision_id"),
            user_id_type=args.get("user_id_type"),
            user_access_token=args.get("user_access_token"),
        )

        result = {
            "document_id": document_id,
            "root_block_id": root_block_id,
        }

        ctx.emit(
            "feishu.docx.root_block.got",
            {
                "tool": "feishu_docx_get_root_block",
                "document_id": document_id,
                "root_block_id": root_block_id,
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_docx_list_child_blocks",
            description="List child blocks under a Feishu Docx block.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "block_id": {"type": "string"},
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                    "document_revision_id": {"type": ["integer", "string"]},
                    "user_id_type": {"type": "string"},
                    "user_access_token": {"type": "string"},
                },
                "required": ["document_id", "block_id"],
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
    def feishu_docx_list_child_blocks(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        document_id = args["document_id"]
        block_id = args["block_id"]

        data = feishu_request(
            "GET",
            f"{DOCX_PREFIX}/documents/{_path(document_id)}/blocks/{_path(block_id)}/children",
            queries=_clean(
                {
                    "page_size": _page_size(args),
                    "page_token": args.get("page_token"),
                    "document_revision_id": args.get("document_revision_id"),
                    "user_id_type": args.get("user_id_type"),
                }
            ),
            user_access_token=args.get("user_access_token"),
        )

        result = _as_page(data)

        ctx.emit(
            "feishu.docx.child_blocks.listed",
            {
                "tool": "feishu_docx_list_child_blocks",
                "document_id": document_id,
                "block_id": block_id,
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_docx_create_blocks",
            description="Create child blocks under a Feishu Docx block.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "block_id": {
                        "type": "string",
                        "description": "Parent block id.",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Insert index. Use -1 to append.",
                    },
                    "children": {"type": "array", "items": {"type": "object"}},
                    "document_revision_id": {"type": ["integer", "string"]},
                    "client_token": {"type": "string"},
                    "user_id_type": {"type": "string"},
                    "user_access_token": {"type": "string"},
                },
                "required": ["document_id", "block_id", "children"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "children": {"type": "array", "items": {"type": "object"}},
                    "document_revision_id": {"type": "integer"},
                    "client_token": {"type": "string"},
                },
                "required": ["children"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_docx_create_blocks(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        document_id = args["document_id"]
        block_id = args["block_id"]

        data = feishu_request(
            "POST",
            f"{DOCX_PREFIX}/documents/{_path(document_id)}/blocks/{_path(block_id)}/children",
            queries=_query(args, "document_revision_id", "client_token", "user_id_type"),
            body={
                "index": args.get("index", -1),
                "children": args.get("children") or [],
            },
            user_access_token=args.get("user_access_token"),
        )

        result = {
            "children": data.get("children") or [],
            "document_revision_id": data.get("document_revision_id"),
            "client_token": _first_str(data, "client_token"),
        }

        ctx.emit(
            "feishu.docx.blocks.created",
            {
                "tool": "feishu_docx_create_blocks",
                "document_id": document_id,
                "block_id": block_id,
                "created_count": len(result["children"]),
                "document_revision_id": result.get("document_revision_id"),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_docx_create_text_blocks",
            description="Create plain text blocks under a Feishu Docx block.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "block_id": {
                        "type": "string",
                        "description": "Parent block id.",
                    },
                    "texts": {"type": "array", "items": {"type": "string"}},
                    "index": {
                        "type": "integer",
                        "description": "Insert index. Use -1 to append.",
                    },
                    "text_style": {"type": "object"},
                    "text_element_style": {"type": "object"},
                    "document_revision_id": {"type": ["integer", "string"]},
                    "client_token": {"type": "string"},
                    "user_id_type": {"type": "string"},
                    "user_access_token": {"type": "string"},
                },
                "required": ["document_id", "block_id", "texts"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "children": {"type": "array", "items": {"type": "object"}},
                    "document_revision_id": {"type": "integer"},
                    "client_token": {"type": "string"},
                },
                "required": ["children"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_docx_create_text_blocks(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        document_id = args["document_id"]
        block_id = args["block_id"]

        children = [
            _text_block(
                text,
                text_style=args.get("text_style"),
                text_element_style=args.get("text_element_style"),
            )
            for text in args.get("texts") or []
        ]

        data = feishu_request(
            "POST",
            f"{DOCX_PREFIX}/documents/{_path(document_id)}/blocks/{_path(block_id)}/children",
            queries=_query(args, "document_revision_id", "client_token", "user_id_type"),
            body={
                "index": args.get("index", -1),
                "children": children,
            },
            user_access_token=args.get("user_access_token"),
        )

        result = {
            "children": data.get("children") or [],
            "document_revision_id": data.get("document_revision_id"),
            "client_token": _first_str(data, "client_token"),
        }

        ctx.emit(
            "feishu.docx.text_blocks.created",
            {
                "tool": "feishu_docx_create_text_blocks",
                "document_id": document_id,
                "block_id": block_id,
                "created_count": len(result["children"]),
                "document_revision_id": result.get("document_revision_id"),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_docx_append_text",
            description="Append plain text blocks to the end of a Feishu Docx document.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "texts": {"type": "array", "items": {"type": "string"}},
                    "text_style": {"type": "object"},
                    "text_element_style": {"type": "object"},
                    "document_revision_id": {"type": ["integer", "string"]},
                    "client_token": {"type": "string"},
                    "user_id_type": {"type": "string"},
                    "user_access_token": {"type": "string"},
                },
                "required": ["document_id", "texts"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "root_block_id": {"type": "string"},
                    "children": {"type": "array", "items": {"type": "object"}},
                    "document_revision_id": {"type": "integer"},
                    "client_token": {"type": "string"},
                },
                "required": ["root_block_id", "children"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_docx_append_text(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        document_id = args["document_id"]

        root_block_id = _find_root_block_id(
            document_id,
            document_revision_id=args.get("document_revision_id"),
            user_id_type=args.get("user_id_type"),
            user_access_token=args.get("user_access_token"),
        )

        children = [
            _text_block(
                text,
                text_style=args.get("text_style"),
                text_element_style=args.get("text_element_style"),
            )
            for text in args.get("texts") or []
        ]

        data = feishu_request(
            "POST",
            f"{DOCX_PREFIX}/documents/{_path(document_id)}/blocks/{_path(root_block_id)}/children",
            queries=_query(args, "document_revision_id", "client_token", "user_id_type"),
            body={
                "index": -1,
                "children": children,
            },
            user_access_token=args.get("user_access_token"),
        )

        result = {
            "root_block_id": root_block_id,
            "children": data.get("children") or [],
            "document_revision_id": data.get("document_revision_id"),
            "client_token": _first_str(data, "client_token"),
        }

        ctx.emit(
            "feishu.docx.text.appended",
            {
                "tool": "feishu_docx_append_text",
                "document_id": document_id,
                "root_block_id": root_block_id,
                "created_count": len(result["children"]),
                "document_revision_id": result.get("document_revision_id"),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_docx_update_block",
            description="Update a Feishu Docx block with a raw update payload.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "block_id": {"type": "string"},
                    "payload": {
                        "type": "object",
                        "description": "Raw Docx block update payload.",
                    },
                    "document_revision_id": {"type": ["integer", "string"]},
                    "client_token": {"type": "string"},
                    "user_id_type": {"type": "string"},
                    "user_access_token": {"type": "string"},
                },
                "required": ["document_id", "block_id", "payload"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "block": {"type": "object"},
                    "document_revision_id": {"type": "integer"},
                    "client_token": {"type": "string"},
                },
                "required": ["block"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_docx_update_block(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        document_id = args["document_id"]
        block_id = args["block_id"]

        data = feishu_request(
            "PATCH",
            f"{DOCX_PREFIX}/documents/{_path(document_id)}/blocks/{_path(block_id)}",
            queries=_query(args, "document_revision_id", "client_token", "user_id_type"),
            body=args.get("payload") or {},
            user_access_token=args.get("user_access_token"),
        )

        block = data.get("block") or {}

        result = {
            "block": block,
            "document_revision_id": data.get("document_revision_id"),
            "client_token": _first_str(data, "client_token"),
        }

        ctx.emit(
            "feishu.docx.block.updated",
            {
                "tool": "feishu_docx_update_block",
                "document_id": document_id,
                "block_id": _first_str(block, "block_id") or block_id,
                "document_revision_id": result.get("document_revision_id"),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_docx_update_text_block",
            description="Replace text elements of a Feishu Docx text block.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "block_id": {"type": "string"},
                    "text": {"type": "string"},
                    "text_element_style": {"type": "object"},
                    "document_revision_id": {"type": ["integer", "string"]},
                    "client_token": {"type": "string"},
                    "user_id_type": {"type": "string"},
                    "user_access_token": {"type": "string"},
                },
                "required": ["document_id", "block_id", "text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "block": {"type": "object"},
                    "document_revision_id": {"type": "integer"},
                    "client_token": {"type": "string"},
                },
                "required": ["block"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_docx_update_text_block(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        document_id = args["document_id"]
        block_id = args["block_id"]

        data = feishu_request(
            "PATCH",
            f"{DOCX_PREFIX}/documents/{_path(document_id)}/blocks/{_path(block_id)}",
            queries=_query(args, "document_revision_id", "client_token", "user_id_type"),
            body={
                "update_text_elements": {
                    "elements": [
                        _text_element(
                            args["text"],
                            args.get("text_element_style"),
                        )
                    ]
                }
            },
            user_access_token=args.get("user_access_token"),
        )

        block = data.get("block") or {}

        result = {
            "block": block,
            "document_revision_id": data.get("document_revision_id"),
            "client_token": _first_str(data, "client_token"),
        }

        ctx.emit(
            "feishu.docx.text_block.updated",
            {
                "tool": "feishu_docx_update_text_block",
                "document_id": document_id,
                "block_id": _first_str(block, "block_id") or block_id,
                "document_revision_id": result.get("document_revision_id"),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_docx_batch_update_blocks",
            description="Batch update Feishu Docx blocks.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "requests": {"type": "array", "items": {"type": "object"}},
                    "document_revision_id": {"type": ["integer", "string"]},
                    "client_token": {"type": "string"},
                    "user_id_type": {"type": "string"},
                    "user_access_token": {"type": "string"},
                },
                "required": ["document_id", "requests"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "blocks": {"type": "array", "items": {"type": "object"}},
                    "document_revision_id": {"type": "integer"},
                    "client_token": {"type": "string"},
                },
                "required": ["blocks"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_docx_batch_update_blocks(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        document_id = args["document_id"]

        data = feishu_request(
            "PATCH",
            f"{DOCX_PREFIX}/documents/{_path(document_id)}/blocks/batch_update",
            queries=_query(args, "document_revision_id", "client_token", "user_id_type"),
            body={"requests": args.get("requests") or []},
            user_access_token=args.get("user_access_token"),
        )

        result = {
            "blocks": data.get("blocks") or [],
            "document_revision_id": data.get("document_revision_id"),
            "client_token": _first_str(data, "client_token"),
        }

        ctx.emit(
            "feishu.docx.blocks.batch_updated",
            {
                "tool": "feishu_docx_batch_update_blocks",
                "document_id": document_id,
                "updated_count": len(result["blocks"]),
                "document_revision_id": result.get("document_revision_id"),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_docx_delete_child_blocks",
            description="Delete child blocks by index range under a Feishu Docx block.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "block_id": {
                        "type": "string",
                        "description": "Parent block id.",
                    },
                    "start_index": {"type": "integer"},
                    "end_index": {
                        "type": "integer",
                        "description": "Exclusive end index.",
                    },
                    "document_revision_id": {"type": ["integer", "string"]},
                    "client_token": {"type": "string"},
                    "user_access_token": {"type": "string"},
                },
                "required": ["document_id", "block_id", "start_index", "end_index"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "document_revision_id": {"type": "integer"},
                    "client_token": {"type": "string"},
                },
                "additionalProperties": False,
            },
        )
    )
    def feishu_docx_delete_child_blocks(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        document_id = args["document_id"]
        block_id = args["block_id"]

        data = feishu_request(
            "DELETE",
            f"{DOCX_PREFIX}/documents/{_path(document_id)}/blocks/{_path(block_id)}/children/batch_delete",
            queries=_query(args, "document_revision_id", "client_token"),
            body={
                "start_index": args["start_index"],
                "end_index": args["end_index"],
            },
            user_access_token=args.get("user_access_token"),
        )

        result = {
            "document_revision_id": data.get("document_revision_id"),
            "client_token": _first_str(data, "client_token"),
        }

        ctx.emit(
            "feishu.docx.child_blocks.deleted",
            {
                "tool": "feishu_docx_delete_child_blocks",
                "document_id": document_id,
                "block_id": block_id,
                "start_index": args["start_index"],
                "end_index": args["end_index"],
                "document_revision_id": result.get("document_revision_id"),
            },
        )

        return result