from __future__ import annotations

from typing import Any


class SchemaError(Exception):
    pass


def validate_schema(schema: dict[str, Any] | None, data: Any, path: str = "$") -> None:
    """Small JSON-schema subset checker for tool validation."""
    if not schema:
        return
    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        errors: list[str] = []
        for candidate_type in expected_type:
            try:
                candidate_schema = dict(schema)
                candidate_schema["type"] = candidate_type
                validate_schema(candidate_schema, data, path)
                return
            except SchemaError as exc:
                errors.append(str(exc))
        allowed = ", ".join(str(t) for t in expected_type)
        detail = "; ".join(errors)
        raise SchemaError(f"{path} should match one of [{allowed}] ({detail})")

    if expected_type == "object":
        if not isinstance(data, dict):
            raise SchemaError(f"{path} should be object")
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in data:
                raise SchemaError(f"{path}.{key} is required")
        for key, value in data.items():
            if key in props:
                validate_schema(props[key], value, f"{path}.{key}")
        return

    if expected_type == "string":
        if not isinstance(data, str):
            raise SchemaError(f"{path} should be string")
        return

    if expected_type == "number":
        if not isinstance(data, (int, float)) or isinstance(data, bool):
            raise SchemaError(f"{path} should be number")
        return

    if expected_type == "integer":
        if not isinstance(data, int) or isinstance(data, bool):
            raise SchemaError(f"{path} should be integer")
        return

    if expected_type == "boolean":
        if not isinstance(data, bool):
            raise SchemaError(f"{path} should be boolean")
        return

    if expected_type == "null":
        if data is not None:
            raise SchemaError(f"{path} should be null")
        return

    if expected_type == "array":
        if not isinstance(data, list):
            raise SchemaError(f"{path} should be array")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(data):
                validate_schema(item_schema, item, f"{path}[{i}]")
        return

    if expected_type is None:
        return

    raise SchemaError(f"unsupported schema type: {expected_type}")
