from typing import Any
from agent_runtime.result import ToolResult


class BaseCreateTool:
    """Base 创建占位；真实实现调用 FeishuBaseAdaptor.batch_create_records。"""
    tool_name = "BaseCreateTool"

    def create(self, *args: Any, **kwargs: Any) -> ToolResult:
        _ = args, kwargs
        return ToolResult(tool_name=self.tool_name, success=True, inputs_summary="scaffold placeholder", outputs_summary="Base 创建占位；真实实现调用 FeishuBaseAdaptor.batch_create_records。")
