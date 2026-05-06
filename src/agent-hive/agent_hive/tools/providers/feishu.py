from __future__ import annotations

import asyncio
import importlib
import os
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from agent_hive.observability.logging import get_logger
from agent_hive.runtime.errors import ToolExecutionError


logger = get_logger("tools.feishu")


DEFAULT_FEISHU_MODULES = [
    "feishu_adapter.feishu_bitable",
    "feishu_adapter.feishu_contact",
    "feishu_adapter.feishu_drive",
    "feishu_adapter.feishu_docx",
    "feishu_adapter.feishu_im_group",
    "feishu_adapter.feishu_im_message",
    "feishu_adapter.feishu_message_tools",
    "feishu_adapter.feishu_wiki",
]


@dataclass
class _BridgeTool:
    spec: Any
    func: Callable[[dict[str, Any], Any], dict[str, Any]]


class _BridgeRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, _BridgeTool] = {}
        self.toolsets: dict[str, set[str]] = {}

    def register(self, spec: Any):
        def wrapper(func: Callable[[dict[str, Any], Any], dict[str, Any]]):
            name = str(getattr(spec, "name"))
            if name in self.tools:
                raise ValueError(f"duplicated Feishu adapter tool: {name}")
            self.tools[name] = _BridgeTool(spec=spec, func=func)
            return func

        return wrapper

    def add_tools_to_toolset(self, toolset: str, tool_names: list[str]) -> None:
        self.toolsets.setdefault(toolset, set()).update(tool_names)

    def get(self, name: str) -> _BridgeTool:
        if name not in self.tools:
            raise KeyError(f"unknown Feishu adapter tool: {name}")
        return self.tools[name]


class _BridgeEventBus:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def publish(self, event: Any) -> None:
        payload = event.model_dump(mode="json") if hasattr(event, "model_dump") else event
        self.events.append(payload if isinstance(payload, dict) else {"event": payload})


class _BridgeToolContext:
    def __init__(self, tool_name: str, event_bus: _BridgeEventBus) -> None:
        self.tool_name = tool_name
        self.call_id = f"call_{uuid4().hex[:12]}"
        self.event_bus = event_bus
        self.config: dict[str, Any] = {}

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.event_bus.publish(
            {
                "event_type": event_type,
                "source": self.tool_name,
                "payload": payload,
                "call_id": self.call_id,
            }
        )

    def checkpoint(self, payload: dict[str, Any]) -> None:
        self.emit("tool.job.checkpoint", payload)

    def is_cancelled(self) -> bool:
        return False

    def raise_if_cancelled(self) -> None:
        return None


class FeishuProvider:
    """Provider wrapper around existing ``src/feishu_adapter`` tools.

    The provider keeps the adapter's tool functions and request semantics
    intact. It only supplies a small registry/context bridge for the new
    runtime.
    """

    provider_name = "feishu"

    def __init__(self, modules: list[str] | None = None) -> None:
        self._registry = _BridgeRegistry()
        self._event_bus = _BridgeEventBus()
        self._modules = modules or DEFAULT_FEISHU_MODULES
        self._loaded = False
        self._im_websocket_enabled = False

    def enable_im_websocket(self) -> None:
        """Enable Feishu IM WS before adapter tools are registered.

        The existing adapter starts IM WebSocket inside
        ``feishu_message_tools.register`` when ``FEISHU_ENABLE_IM_WS=1``. This
        method only prepares that flag; the actual start still happens during
        provider tool initialization.
        """

        was_enabled = self._im_websocket_enabled
        self._im_websocket_enabled = True
        os.environ["FEISHU_ENABLE_IM_WS"] = "1"
        if self._loaded and not was_enabled:
            logger.warning("Feishu IM WebSocket was enabled after provider load; restart the daemon to register WS listener")

    def load(self) -> None:
        if self._loaded:
            return
        logger.info(
            "loading Feishu adapter tools modules=%s im_ws_enabled=%s app_id_present=%s app_secret_present=%s",
            len(self._modules),
            self._im_websocket_enabled,
            bool(os.environ.get("FEISHU_APP_ID")),
            bool(os.environ.get("FEISHU_APP_SECRET")),
        )
        for module_name in self._modules:
            module = importlib.import_module(module_name)
            register = getattr(module, "register", None)
            if callable(register):
                logger.debug("registering Feishu adapter module=%s", module_name)
                register(self._registry, event_bus=self._event_bus)
        self._loaded = True
        logger.info("Feishu adapter tools loaded tool_count=%s", len(self._registry.tools))

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.load()
        bridge_tool = self._registry.get(tool_name)
        ctx = _BridgeToolContext(tool_name, self._event_bus)
        try:
            logger.info("calling Feishu tool tool_name=%s argument_keys=%s", tool_name, sorted(arguments))
            return await asyncio.to_thread(bridge_tool.func, arguments, ctx)
        except Exception as exc:
            logger.exception("Feishu tool failed tool_name=%s error=%s", tool_name, exc)
            raise ToolExecutionError(f"{tool_name} failed: {exc}") from exc

    def list_tools(self) -> list[str]:
        self.load()
        return sorted(self._registry.tools)

    def drain_events(self) -> list[dict[str, Any]]:
        events = list(self._event_bus.events)
        self._event_bus.events.clear()
        if events:
            logger.debug("drained Feishu adapter events count=%s", len(events))
        return events
