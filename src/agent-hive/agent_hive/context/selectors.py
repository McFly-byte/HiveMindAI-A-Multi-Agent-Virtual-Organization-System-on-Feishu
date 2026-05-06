from __future__ import annotations

from typing import Any


def select_memory_contents(memories: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("content") or item) for item in memories]
