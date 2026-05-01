from typing import Any
from agent_runtime.result import ToolResult


class TraceTool:
    """Trace 写入占位；实际由 TraceService 落本地 jsonl。"""
    tool_name = "TraceTool"

    def write(self, *args: Any, **kwargs: Any) -> ToolResult:
        _ = args, kwargs
        return ToolResult(tool_name=self.tool_name, success=True, inputs_summary="scaffold placeholder", outputs_summary="Trace 写入占位；实际由 TraceService 落本地 jsonl。")
