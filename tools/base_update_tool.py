from typing import Any
from agent_runtime.result import ToolResult


class BaseUpdateTool:
    """Base 更新占位；真实实现调用 FeishuBaseAdaptor.batch_update_records。"""
    tool_name = "BaseUpdateTool"

    def update(self, *args: Any, **kwargs: Any) -> ToolResult:
        _ = args, kwargs
        return ToolResult(tool_name=self.tool_name, success=True, inputs_summary="scaffold placeholder", outputs_summary="Base 更新占位；真实实现调用 FeishuBaseAdaptor.batch_update_records。")
