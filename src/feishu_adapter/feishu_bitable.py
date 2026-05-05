from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse
import re

from tool_integration.events import EventBus
from tool_integration.tools import ToolRegistry, ToolSpec

from feishu_adapter.feishu_client import feishu_request


DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


_BASE_TOKEN_RE = re.compile(r"/(?:base|bitable)/([A-Za-z0-9_-]+)")
_APP_TOKEN_RE = re.compile(r"(?:app_token|base_token)=([A-Za-z0-9_-]+)")
_TABLE_ID_RE = re.compile(r"(?:table|table_id)=([A-Za-z0-9_-]+)")
_VIEW_ID_RE = re.compile(r"(?:view|view_id)=([A-Za-z0-9_-]+)")


def _first_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _first_int(data: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        if isinstance(value, int):
            return value
    return 0


def _first_bool(data: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            return value
    return False


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _items(data: dict[str, Any]) -> list[dict[str, Any]]:
    value = data.get("items") or data.get("records") or data.get("tables") or data.get("fields") or data.get("views") or []
    return value if isinstance(value, list) else []


def _page_size(args: dict[str, Any], default: int = DEFAULT_PAGE_SIZE) -> int:
    value = args.get("page_size", default)
    if not isinstance(value, int):
        return default
    return max(1, min(value, MAX_PAGE_SIZE))


def _clean_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in data.items()
        if value is not None and value != ""
    }


def _page_result(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "items": _items(data),
        "has_more": bool(data.get("has_more", False)),
        "page_token": _first_str(data, "page_token", "next_page_token"),
    }


def _app_payload(data: dict[str, Any]) -> dict[str, Any]:
    app = _as_dict(data.get("app")) or data
    return {
        "app_token": _first_str(app, "app_token", "token"),
        "name": _first_str(app, "name", "title"),
        "folder_token": _first_str(app, "folder_token"),
        "url": _first_str(app, "url"),
        "revision": _first_int(app, "revision"),
    }


def _table_payload(data: dict[str, Any]) -> dict[str, Any]:
    table = _as_dict(data.get("table")) or data
    return {
        "table_id": _first_str(table, "table_id"),
        "name": _first_str(table, "name"),
        "revision": _first_int(table, "revision"),
    }


def _field_payload(data: dict[str, Any]) -> dict[str, Any]:
    field = _as_dict(data.get("field")) or data
    return {
        "field_id": _first_str(field, "field_id"),
        "field_name": _first_str(field, "field_name", "name"),
        "field_type": _first_int(field, "type", "field_type"),
        "property": _as_dict(field.get("property")),
    }


def _record_payload(data: dict[str, Any]) -> dict[str, Any]:
    record = _as_dict(data.get("record")) or data
    return {
        "record_id": _first_str(record, "record_id"),
        "fields": _as_dict(record.get("fields")),
    }


def _view_payload(data: dict[str, Any]) -> dict[str, Any]:
    view = _as_dict(data.get("view")) or data
    return {
        "view_id": _first_str(view, "view_id"),
        "view_name": _first_str(view, "view_name", "name"),
        "view_type": _first_str(view, "view_type", "type"),
        "property": _as_dict(view.get("property")),
    }


def _parse_bitable_url(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    app_token = ""
    table_id = ""
    view_id = ""

    base_match = _BASE_TOKEN_RE.search(parsed.path)
    if base_match:
        app_token = base_match.group(1)

    if not app_token:
        app_match = _APP_TOKEN_RE.search(url)
        if app_match:
            app_token = app_match.group(1)

    table_values = query.get("table") or query.get("table_id") or []
    if table_values:
        table_id = table_values[0]
    else:
        table_match = _TABLE_ID_RE.search(url)
        if table_match:
            table_id = table_match.group(1)

    view_values = query.get("view") or query.get("view_id") or []
    if view_values:
        view_id = view_values[0]
    else:
        view_match = _VIEW_ID_RE.search(url)
        if view_match:
            view_id = view_match.group(1)

    return {
        "app_token": app_token,
        "table_id": table_id,
        "view_id": view_id,
    }


def register(registry: ToolRegistry, event_bus: EventBus | None = None):
    @registry.register(
        ToolSpec(
            name="feishu_bitable_parse_url",
            description="Parse a Feishu Bitable URL and extract app_token, table_id and view_id.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "view_id": {"type": "string"},
                },
                "required": ["app_token"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_parse_url(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        result = _parse_bitable_url(args["url"])

        ctx.emit(
            "feishu.bitable.url.parsed",
            {
                "tool": "feishu_bitable_parse_url",
                "app_token": result["app_token"],
                "table_id": result["table_id"],
                "view_id": result["view_id"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_create_app",
            description="Create a Feishu Bitable app in a Drive folder.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "folder_token": {
                        "type": "string",
                        "description": "Parent Drive folder token. Empty means default location if API allows it.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "name": {"type": "string"},
                    "folder_token": {"type": "string"},
                    "url": {"type": "string"},
                    "revision": {"type": "integer"},
                },
                "required": ["app_token"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_create_app(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        body = _clean_dict({
            "name": args["name"],
            "folder_token": args.get("folder_token"),
        })

        data = feishu_request(
            "POST",
            "/open-apis/bitable/v1/apps",
            body=body,
        )

        result = _app_payload(data)
        if not result["name"]:
            result["name"] = args["name"]
        if not result["folder_token"]:
            result["folder_token"] = args.get("folder_token", "")

        ctx.emit(
            "feishu.bitable.app.created",
            {
                "tool": "feishu_bitable_create_app",
                "app_token": result["app_token"],
                "name": result["name"],
                "folder_token": result["folder_token"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_get_app",
            description="Get Feishu Bitable app metadata.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                },
                "required": ["app_token"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "name": {"type": "string"},
                    "folder_token": {"type": "string"},
                    "url": {"type": "string"},
                    "revision": {"type": "integer"},
                },
                "required": ["app_token"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_get_app(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]

        data = feishu_request(
            "GET",
            f"/open-apis/bitable/v1/apps/{app_token}",
        )

        result = _app_payload(data)
        if not result["app_token"]:
            result["app_token"] = app_token

        ctx.emit(
            "feishu.bitable.app.got",
            {
                "tool": "feishu_bitable_get_app",
                "app_token": app_token,
                "name": result["name"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_list_tables",
            description="List tables in a Feishu Bitable app.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                },
                "required": ["app_token"],
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
    def feishu_bitable_list_tables(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]

        data = feishu_request(
            "GET",
            f"/open-apis/bitable/v1/apps/{app_token}/tables",
            queries={
                "page_size": _page_size(args),
                "page_token": args.get("page_token"),
            },
        )

        result = _page_result(data)

        ctx.emit(
            "feishu.bitable.tables.listed",
            {
                "tool": "feishu_bitable_list_tables",
                "app_token": app_token,
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_create_table",
            description="Create a table in a Feishu Bitable app.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "name": {"type": "string"},
                    "default_view_name": {"type": "string"},
                    "fields": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Optional initial field definitions, each with field_name, type and optional property.",
                    },
                },
                "required": ["app_token", "name"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "table_id": {"type": "string"},
                    "name": {"type": "string"},
                    "revision": {"type": "integer"},
                },
                "required": ["table_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_create_table(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]

        table = _clean_dict({
            "name": args["name"],
            "default_view_name": args.get("default_view_name"),
            "fields": args.get("fields"),
        })

        data = feishu_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables",
            body={"table": table},
        )

        result = _table_payload(data)
        if not result["name"]:
            result["name"] = args["name"]

        ctx.emit(
            "feishu.bitable.table.created",
            {
                "tool": "feishu_bitable_create_table",
                "app_token": app_token,
                "table_id": result["table_id"],
                "name": result["name"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_update_table",
            description="Update a Feishu Bitable table name.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["app_token", "table_id", "name"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "table_id": {"type": "string"},
                    "name": {"type": "string"},
                    "revision": {"type": "integer"},
                },
                "required": ["table_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_update_table(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]
        name = args["name"]

        data = feishu_request(
            "PATCH",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}",
            body={"name": name},
        )

        result = _table_payload(data)
        if not result["table_id"]:
            result["table_id"] = table_id
        if not result["name"]:
            result["name"] = name

        ctx.emit(
            "feishu.bitable.table.updated",
            {
                "tool": "feishu_bitable_update_table",
                "app_token": app_token,
                "table_id": table_id,
                "name": name,
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_delete_tables",
            description="Batch delete tables from a Feishu Bitable app.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["app_token", "table_ids"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "deleted_table_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["deleted_table_ids"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_delete_tables(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_ids = _as_list(args.get("table_ids"))

        feishu_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/batch_delete",
            body={"table_ids": table_ids},
        )

        result = {"deleted_table_ids": table_ids}

        ctx.emit(
            "feishu.bitable.tables.deleted",
            {
                "tool": "feishu_bitable_delete_tables",
                "app_token": app_token,
                "count": len(table_ids),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_list_fields",
            description="List fields in a Feishu Bitable table.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "view_id": {"type": "string"},
                    "text_field_as_array": {"type": "boolean"},
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                },
                "required": ["app_token", "table_id"],
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
    def feishu_bitable_list_fields(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]

        data = feishu_request(
            "GET",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            queries={
                "view_id": args.get("view_id"),
                "text_field_as_array": args.get("text_field_as_array"),
                "page_size": _page_size(args),
                "page_token": args.get("page_token"),
            },
        )

        result = _page_result(data)

        ctx.emit(
            "feishu.bitable.fields.listed",
            {
                "tool": "feishu_bitable_list_fields",
                "app_token": app_token,
                "table_id": table_id,
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_create_field",
            description="Create a field in a Feishu Bitable table.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "field_name": {"type": "string"},
                    "field_type": {"type": "integer"},
                    "property": {"type": "object"},
                },
                "required": ["app_token", "table_id", "field_name", "field_type"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "field_id": {"type": "string"},
                    "field_name": {"type": "string"},
                    "field_type": {"type": "integer"},
                    "property": {"type": "object"},
                },
                "required": ["field_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_create_field(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]

        body = _clean_dict({
            "field_name": args["field_name"],
            "type": args["field_type"],
            "property": args.get("property"),
        })

        data = feishu_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            body=body,
        )

        result = _field_payload(data)
        if not result["field_name"]:
            result["field_name"] = args["field_name"]
        if not result["field_type"]:
            result["field_type"] = args["field_type"]

        ctx.emit(
            "feishu.bitable.field.created",
            {
                "tool": "feishu_bitable_create_field",
                "app_token": app_token,
                "table_id": table_id,
                "field_id": result["field_id"],
                "field_name": result["field_name"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_update_field",
            description="Update a field in a Feishu Bitable table.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "field_id": {"type": "string"},
                    "field_name": {"type": "string"},
                    "field_type": {"type": "integer"},
                    "property": {"type": "object"},
                },
                "required": ["app_token", "table_id", "field_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "field_id": {"type": "string"},
                    "field_name": {"type": "string"},
                    "field_type": {"type": "integer"},
                    "property": {"type": "object"},
                },
                "required": ["field_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_update_field(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]
        field_id = args["field_id"]

        body = _clean_dict({
            "field_name": args.get("field_name"),
            "type": args.get("field_type"),
            "property": args.get("property"),
        })
        if not body:
            raise ValueError("at least one of field_name, field_type or property is required")

        data = feishu_request(
            "PUT",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}",
            body=body,
        )

        result = _field_payload(data)
        if not result["field_id"]:
            result["field_id"] = field_id

        ctx.emit(
            "feishu.bitable.field.updated",
            {
                "tool": "feishu_bitable_update_field",
                "app_token": app_token,
                "table_id": table_id,
                "field_id": field_id,
                "field_name": result["field_name"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_delete_field",
            description="Delete a field from a Feishu Bitable table.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "field_id": {"type": "string"},
                },
                "required": ["app_token", "table_id", "field_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "field_id": {"type": "string"},
                    "deleted": {"type": "boolean"},
                },
                "required": ["field_id", "deleted"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_delete_field(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]
        field_id = args["field_id"]

        feishu_request(
            "DELETE",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}",
        )

        result = {"field_id": field_id, "deleted": True}

        ctx.emit(
            "feishu.bitable.field.deleted",
            {
                "tool": "feishu_bitable_delete_field",
                "app_token": app_token,
                "table_id": table_id,
                "field_id": field_id,
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_search_records",
            description="Search records in a Feishu Bitable table with optional view, fields, filter and sort.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "view_id": {"type": "string"},
                    "field_names": {"type": "array", "items": {"type": "string"}},
                    "filter": {"type": "object"},
                    "sort": {"type": "array", "items": {"type": "object"}},
                    "automatic_fields": {"type": "boolean"},
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                },
                "required": ["app_token", "table_id"],
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
    def feishu_bitable_search_records(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]

        body = _clean_dict({
            "view_id": args.get("view_id"),
            "field_names": args.get("field_names"),
            "filter": args.get("filter"),
            "sort": args.get("sort"),
            "automatic_fields": args.get("automatic_fields"),
        })

        data = feishu_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
            queries={
                "page_size": _page_size(args),
                "page_token": args.get("page_token"),
            },
            body=body,
        )

        result = _page_result(data)

        ctx.emit(
            "feishu.bitable.records.searched",
            {
                "tool": "feishu_bitable_search_records",
                "app_token": app_token,
                "table_id": table_id,
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_batch_get_records",
            description="Batch get records from a Feishu Bitable table by record_id.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "record_ids": {"type": "array", "items": {"type": "string"}},
                    "automatic_fields": {"type": "boolean"},
                },
                "required": ["app_token", "table_id", "record_ids"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_batch_get_records(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]
        record_ids = _as_list(args.get("record_ids"))

        body = _clean_dict({
            "record_ids": record_ids,
            "automatic_fields": args.get("automatic_fields"),
        })

        data = feishu_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_get",
            body=body,
        )

        result = {"items": _items(data)}

        ctx.emit(
            "feishu.bitable.records.batch_got",
            {
                "tool": "feishu_bitable_batch_get_records",
                "app_token": app_token,
                "table_id": table_id,
                "count": len(result["items"]),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_create_record",
            description="Create one record in a Feishu Bitable table.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "fields": {"type": "object"},
                },
                "required": ["app_token", "table_id", "fields"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "record_id": {"type": "string"},
                    "fields": {"type": "object"},
                },
                "required": ["record_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_create_record(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]

        data = feishu_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            body={"fields": args["fields"]},
        )

        result = _record_payload(data)

        ctx.emit(
            "feishu.bitable.record.created",
            {
                "tool": "feishu_bitable_create_record",
                "app_token": app_token,
                "table_id": table_id,
                "record_id": result["record_id"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_batch_create_records",
            description="Batch create records in a Feishu Bitable table.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "records": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Each item should be {fields: {...}}.",
                    },
                },
                "required": ["app_token", "table_id", "records"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_batch_create_records(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]
        records = _as_list(args.get("records"))

        data = feishu_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            body={"records": records},
        )

        result = {"items": _items(data)}

        ctx.emit(
            "feishu.bitable.records.batch_created",
            {
                "tool": "feishu_bitable_batch_create_records",
                "app_token": app_token,
                "table_id": table_id,
                "count": len(result["items"]),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_update_record",
            description="Update one record in a Feishu Bitable table.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "record_id": {"type": "string"},
                    "fields": {"type": "object"},
                },
                "required": ["app_token", "table_id", "record_id", "fields"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "record_id": {"type": "string"},
                    "fields": {"type": "object"},
                },
                "required": ["record_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_update_record(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]
        record_id = args["record_id"]

        data = feishu_request(
            "PUT",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            body={"fields": args["fields"]},
        )

        result = _record_payload(data)
        if not result["record_id"]:
            result["record_id"] = record_id

        ctx.emit(
            "feishu.bitable.record.updated",
            {
                "tool": "feishu_bitable_update_record",
                "app_token": app_token,
                "table_id": table_id,
                "record_id": record_id,
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_batch_update_records",
            description="Batch update records in a Feishu Bitable table.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "records": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Each item should be {record_id: ..., fields: {...}}.",
                    },
                },
                "required": ["app_token", "table_id", "records"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_batch_update_records(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]
        records = _as_list(args.get("records"))

        data = feishu_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update",
            body={"records": records},
        )

        result = {"items": _items(data)}

        ctx.emit(
            "feishu.bitable.records.batch_updated",
            {
                "tool": "feishu_bitable_batch_update_records",
                "app_token": app_token,
                "table_id": table_id,
                "count": len(result["items"]),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_delete_records",
            description="Batch delete records from a Feishu Bitable table.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "record_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["app_token", "table_id", "record_ids"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "deleted_record_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["deleted_record_ids"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_delete_records(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]
        record_ids = _as_list(args.get("record_ids"))

        feishu_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete",
            body={"records": record_ids},
        )

        result = {"deleted_record_ids": record_ids}

        ctx.emit(
            "feishu.bitable.records.deleted",
            {
                "tool": "feishu_bitable_delete_records",
                "app_token": app_token,
                "table_id": table_id,
                "count": len(record_ids),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_list_views",
            description="List views in a Feishu Bitable table.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                },
                "required": ["app_token", "table_id"],
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
    def feishu_bitable_list_views(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]

        data = feishu_request(
            "GET",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/views",
            queries={
                "page_size": _page_size(args),
                "page_token": args.get("page_token"),
            },
        )

        result = _page_result(data)

        ctx.emit(
            "feishu.bitable.views.listed",
            {
                "tool": "feishu_bitable_list_views",
                "app_token": app_token,
                "table_id": table_id,
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_bitable_get_view",
            description="Get a Feishu Bitable table view.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "app_token": {"type": "string"},
                    "table_id": {"type": "string"},
                    "view_id": {"type": "string"},
                },
                "required": ["app_token", "table_id", "view_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "view_id": {"type": "string"},
                    "view_name": {"type": "string"},
                    "view_type": {"type": "string"},
                    "property": {"type": "object"},
                },
                "required": ["view_id"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_bitable_get_view(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        app_token = args["app_token"]
        table_id = args["table_id"]
        view_id = args["view_id"]

        data = feishu_request(
            "GET",
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view_id}",
        )

        result = _view_payload(data)
        if not result["view_id"]:
            result["view_id"] = view_id

        ctx.emit(
            "feishu.bitable.view.got",
            {
                "tool": "feishu_bitable_get_view",
                "app_token": app_token,
                "table_id": table_id,
                "view_id": view_id,
                "view_name": result["view_name"],
            },
        )

        return result
