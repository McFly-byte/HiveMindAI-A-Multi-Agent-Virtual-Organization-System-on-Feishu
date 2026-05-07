from __future__ import annotations

from typing import Any, Protocol


class ToolProvider(Protocol):
    provider_name: str

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        ...
