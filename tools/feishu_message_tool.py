from typing import Any
from agent_runtime.result import ToolResult


class FeishuMessageTool:
    """飞书消息工具占位。"""
    tool_name = "FeishuMessageTool"

    def send(self, *args: Any, **kwargs: Any) -> ToolResult:
        _ = args, kwargs
        return ToolResult(tool_name=self.tool_name, success=True, inputs_summary="scaffold placeholder", outputs_summary="飞书消息工具占位。")
