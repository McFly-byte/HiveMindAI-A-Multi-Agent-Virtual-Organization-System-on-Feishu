from __future__ import annotations

from datetime import date, datetime
from typing import Any


def _cell_scalar(cell: Any) -> Any:
    if isinstance(cell, dict):
        for key in ("value", "text", "name", "link", "url", "timestamp", "date"):
            if key in cell:
                return cell[key]
    return cell


def _cell_text(cell: Any) -> str | None:
    if cell is None:
        return None
    if isinstance(cell, str):
        return cell
    if isinstance(cell, (int, float, bool)):
        return str(cell)
    if isinstance(cell, dict):
        if "text" in cell and isinstance(cell["text"], str):
            return cell["text"]
        if "value" in cell:
            return _cell_text(cell["value"])
        if cell.get("type") == 1 and isinstance(cell.get("value"), list):
            parts: list[str] = []
            for item in cell["value"]:
                if isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
            if parts:
                return "".join(parts)
    if isinstance(cell, list):
        texts = [_cell_text(c) for c in cell]
        return ", ".join(t for t in texts if t)
    return None


def _parse_date_like(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Feishu date fields normally use millisecond timestamps.
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds).date()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return _parse_date_like(int(text))
        normalized = text.replace("/", "-").replace(".", "-")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        for fmt, width in (("%Y-%m-%d", 10), ("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16)):
            try:
                return datetime.strptime(normalized[:width], fmt).date()
            except ValueError:
                pass
        try:
            return datetime.fromisoformat(normalized).date()
        except ValueError:
            return None
    if isinstance(value, dict):
        for key in ("value", "timestamp", "date", "text"):
            parsed = _parse_date_like(value.get(key))
            if parsed is not None:
                return parsed
    if isinstance(value, list):
        for item in value:
            parsed = _parse_date_like(item)
            if parsed is not None:
                return parsed
    return None


def _parse_number_like(value: Any) -> float | None:
    value = _cell_scalar(value)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    if isinstance(value, list):
        for item in value:
            parsed = _parse_number_like(item)
            if parsed is not None:
                return parsed
    return None


def record_field_text(fields: dict[str, Any], *names: str) -> str | None:
    """Best-effort read of a Feishu Bitable ``fields`` map (Chinese field names)."""

    for name in names:
        if name in fields:
            t = _cell_text(fields[name])
            if t:
                return t
    return None


def record_field_date(fields: dict[str, Any], *names: str) -> date | None:
    for name in names:
        if name in fields:
            parsed = _parse_date_like(fields[name])
            if parsed is not None:
                return parsed
    return None


def record_field_number(fields: dict[str, Any], *names: str) -> float | None:
    for name in names:
        if name in fields:
            parsed = _parse_number_like(fields[name])
            if parsed is not None:
                return parsed
    return None


def record_field_bool(fields: dict[str, Any], *names: str) -> bool | None:
    for name in names:
        if name not in fields:
            continue
        value = _cell_scalar(fields[name])
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return bool(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"1", "true", "yes", "y", "checked", "是", "已是", "需要"}:
                return True
            if text in {"0", "false", "no", "n", "unchecked", "否", "不需要"}:
                return False
        if isinstance(value, list) and value:
            parsed = record_field_bool({name: value[0]}, name)
            if parsed is not None:
                return parsed
    return None


def record_field_texts(fields: dict[str, Any], *names: str) -> list[str]:
    for name in names:
        if name not in fields:
            continue
        raw = fields[name]
        if isinstance(raw, list):
            texts = [_cell_text(item) for item in raw]
            return [text for text in texts if text]
        text = _cell_text(raw)
        if text:
            return [part.strip() for part in text.split(",") if part.strip()]
    return []
