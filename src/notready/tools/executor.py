from __future__ import annotations

import traceback
from typing import Callable

from runtime.models import now_hms
from tools.registry import ToolRegistry
from tools.spec import ToolCallRequest, ToolCallResult


class ToolExecutor:
    """Execute application tools only.

    LLM tool plans are treated as untrusted input. Every requested tool must be
    registered and must use application identity.
    """

    def __init__(self, registry: ToolRegistry, log_hook: Callable[[dict], None] | None = None) -> None:
        self.registry = registry
        self.log_hook = log_hook

    def call(self, req: ToolCallRequest) -> ToolCallResult:
        spec = self.registry.get(req.tool_name)
        self._log(f"tool.call.request · {req.tool_name} · by {req.requested_by}")
        if not spec:
            error = f"unknown app tool: {req.tool_name}"
            self._log(f"tool.call.error · {error}")
            return ToolCallResult(ok=False, error=error)
        if not spec.is_app_tool:
            error = f"forbidden non-app tool: {req.tool_name}"
            self._log(f"tool.call.error · {error}")
            return ToolCallResult(ok=False, error=error)
        try:
            data = spec.handler(req.args)
            self._log(f"tool.call.result · {req.tool_name} · ok")
            return ToolCallResult(ok=True, data=data)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._log(f"tool.call.error · {req.tool_name} · {error}")
            return ToolCallResult(ok=False, error=error + "\n" + traceback.format_exc(limit=2))

    def _log(self, message: str) -> None:
        if self.log_hook:
            self.log_hook({"message": message, "time": now_hms()})
