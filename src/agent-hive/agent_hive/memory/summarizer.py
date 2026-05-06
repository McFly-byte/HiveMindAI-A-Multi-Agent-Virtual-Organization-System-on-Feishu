from __future__ import annotations

from typing import Any


def summarize_memory_records(records: list[dict[str, Any]], *, limit: int = 1600) -> str:
    text = "\n".join(str(item.get("content") or item) for item in records)
    return text if len(text) <= limit else text[: limit - 3] + "..."
