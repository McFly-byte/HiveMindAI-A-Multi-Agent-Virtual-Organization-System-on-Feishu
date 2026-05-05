"""MVP wiring: tool integration, handlers, trace sink, quality gate."""

from agent_runtime.mvp.builder import build_runtime_with_tool_integration, default_tool_scan_dirs

__all__ = ["build_runtime_with_tool_integration", "default_tool_scan_dirs"]
