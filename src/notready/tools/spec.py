from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    args_schema: dict[str, Any]
    side_effect: bool
    handler: ToolHandler
    timeout_sec: int = 30
    auth_type: str = "app"

    @property
    def is_app_tool(self) -> bool:
        return self.auth_type == "app"


@dataclass
class ToolCallRequest:
    tool_name: str
    args: dict[str, Any]
    requested_by: str
    session_id: str | None = None
    dialogue_id: str | None = None


@dataclass
class ToolCallResult:
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None
