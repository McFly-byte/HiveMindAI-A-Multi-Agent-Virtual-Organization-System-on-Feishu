from __future__ import annotations

from typing import Any


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


def record_field_text(fields: dict[str, Any], *names: str) -> str | None:
    """Best-effort read of a Feishu Bitable ``fields`` map (Chinese field names)."""

    for name in names:
        if name in fields:
            t = _cell_text(fields[name])
            if t:
                return t
    return None
