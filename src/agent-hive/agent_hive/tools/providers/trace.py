from __future__ import annotations

from typing import Any


class TraceProvider:
    provider_name = "trace"

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        event = {"tool": tool_name, **arguments}
        self.events.append(event)
        return {"ok": True, "event": event}
