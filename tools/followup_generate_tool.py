from typing import Any
from agent_runtime.result import ToolResult


class FollowUpGenerateTool:
    """追问生成占位；后续针对负责人、任务和缺口生成追问。"""
    tool_name = "FollowUpGenerateTool"

    def generate(self, *args: Any, **kwargs: Any) -> ToolResult:
        _ = args, kwargs
        return ToolResult(tool_name=self.tool_name, success=True, inputs_summary="scaffold placeholder", outputs_summary="追问生成占位；后续针对负责人、任务和缺口生成追问。")
