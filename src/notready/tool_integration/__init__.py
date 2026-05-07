"""Tool execution and registration layer.

External tool modules should import types from ``tool_integration.tools``
and optionally use ``scan_tool_dirs`` / ``ToolRuntime`` from this package."""

from tool_integration.events import (
    EventBus,
    ToolEventScope,
    ToolIntegrationEvent,
    ToolIntegrationEventType,
)
from tool_integration.loader import (
    env_config,
    load_dotenv_if_present,
    resolve_tool_scan_dirs,
    scan_tool_dirs,
)
from tool_integration.runtime import ToolRuntime
from tool_integration.tools import ToolContext, ToolRegistry, ToolSpec

__all__ = [
    "ToolRegistry",
    "ToolSpec",
    "ToolContext",
    "ToolRuntime",
    "scan_tool_dirs",
    "resolve_tool_scan_dirs",
    "load_dotenv_if_present",
    "env_config",
    "EventBus",
    "ToolIntegrationEvent",
    "ToolIntegrationEventType",
    "ToolEventScope",
]
