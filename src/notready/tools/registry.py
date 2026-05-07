from __future__ import annotations

from tools.spec import ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        if not spec.is_app_tool:
            raise ValueError(f"only app tools can be registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def list_app_tools(self) -> list[ToolSpec]:
        return [tool for tool in self._tools.values() if tool.is_app_tool]

    def llm_catalog(self) -> list[dict[str, object]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "args_schema": tool.args_schema,
                "side_effect": tool.side_effect,
                "auth_type": tool.auth_type,
            }
            for tool in self.list_app_tools()
        ]
