from typing import Any
from agent_runtime.result import ToolResult


class LLMTool:
    """LLM JSON 生成占位；真实实现需供应商适配和 Pydantic 校验。"""
    tool_name = "LLMTool"

    def generate_json(self, *args: Any, **kwargs: Any) -> ToolResult:
        _ = args, kwargs
        return ToolResult(tool_name=self.tool_name, success=True, inputs_summary="scaffold placeholder", outputs_summary="LLM JSON 生成占位；真实实现需供应商适配和 Pydantic 校验。")
