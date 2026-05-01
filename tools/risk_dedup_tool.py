from typing import Any
from agent_runtime.result import ToolResult


class RiskDedupTool:
    """风险去重占位；后续按项目、任务、类型、证据做幂等。"""
    tool_name = "RiskDedupTool"

    def check(self, *args: Any, **kwargs: Any) -> ToolResult:
        _ = args, kwargs
        return ToolResult(tool_name=self.tool_name, success=True, inputs_summary="scaffold placeholder", outputs_summary="风险去重占位；后续按项目、任务、类型、证据做幂等。")
