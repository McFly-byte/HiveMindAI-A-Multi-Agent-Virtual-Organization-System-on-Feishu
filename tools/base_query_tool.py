from typing import Any
from agent_runtime.result import ToolResult


class BaseQueryTool:
    """Base 查询占位；真实实现调用 FeishuBaseAdaptor.list_records。"""
    tool_name = "BaseQueryTool"

    def query(self, *args: Any, **kwargs: Any) -> ToolResult:
        _ = args, kwargs
        return ToolResult(tool_name=self.tool_name, success=True, inputs_summary="scaffold placeholder", outputs_summary="Base 查询占位；真实实现调用 FeishuBaseAdaptor.list_records。")
