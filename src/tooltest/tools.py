from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Literal
import time
import uuid

from .events import Event, EventBus

ToolMode = Literal["sync", "async", "switchable"]
ToolKind = Literal["business", "meta"]


@dataclass
class SwitchPolicy:
    foreground_timeout_seconds: float = 2.0
    check_timeout_seconds: float = 0.25
    max_runtime_seconds: float | None = None
    allow_user_switch: bool = True
    allow_runtime_auto_switch: bool = True


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    mode: ToolMode = "sync"
    kind: ToolKind = "business"
    version: str = "0.1.0"
    switch_policy: SwitchPolicy | None = None
    events: list[str] = field(default_factory=list)


@dataclass
class Tool:
    spec: ToolSpec
    func: Callable[[dict[str, Any], "ToolContext"], dict[str, Any]]


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}
        self.toolsets: dict[str, set[str]] = {}

    def register(self, spec: ToolSpec):
        def wrapper(func: Callable[[dict[str, Any], "ToolContext"], dict[str, Any]]):
            if spec.name in self.tools:
                raise ValueError(f"duplicated tool: {spec.name}")
            self.tools[spec.name] = Tool(spec=spec, func=func)
            return func
        return wrapper

    def add(self, spec: ToolSpec, func: Callable[[dict[str, Any], "ToolContext"], dict[str, Any]]):
        if spec.name in self.tools:
            raise ValueError(f"duplicated tool: {spec.name}")
        self.tools[spec.name] = Tool(spec=spec, func=func)

    def get(self, name: str) -> Tool:
        if name not in self.tools:
            raise KeyError(f"unknown tool: {name}")
        return self.tools[name]

    def list(self, include_meta: bool = True) -> list[Tool]:
        result = list(self.tools.values())
        if not include_meta:
            result = [t for t in result if t.spec.kind != "meta"]
        return result

    def to_llm_tools(self, allowed_tools: list[str] | None = None, include_meta: bool = True) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        allowed = set(allowed_tools or [])
        for tool in self.tools.values():
            if allowed_tools and tool.spec.name not in allowed:
                continue
            if not include_meta and tool.spec.kind == "meta":
                continue
            out.append({
                "type": "function",
                "function": {
                    "name": tool.spec.name,
                    "description": tool.spec.description,
                    "parameters": tool.spec.input_schema,
                },
            })
        return out

    def specs_as_dicts(self) -> list[dict[str, Any]]:
        specs = []
        for tool in self.tools.values():
            d = asdict(tool.spec)
            specs.append(d)
        return specs

    def add_tools_to_toolset(self, toolset: str, tool_names: list[str]):
        if not toolset:
            return
        bucket = self.toolsets.setdefault(toolset, set())
        bucket.update(tool_names)

    def list_toolsets(self) -> dict[str, list[str]]:
        return {k: sorted(v) for k, v in self.toolsets.items()}

    def tools_in_toolsets(self, toolsets: list[str]) -> set[str]:
        names: set[str] = set()
        for ts in toolsets:
            names.update(self.toolsets.get(ts, set()))
        return names


class ToolContext:
    def __init__(
        self,
        tool_name: str,
        event_bus: EventBus,
        call_id: str,
        job_runtime: Any | None = None,
        job_id: str | None = None,
        config: dict[str, Any] | None = None,
    ):
        self.tool_name = tool_name
        self.event_bus = event_bus
        self.call_id = call_id
        self.job_runtime = job_runtime
        self.job_id = job_id
        self.config = config or {}

    def emit(self, event_type: str, payload: dict[str, Any]):
        self.event_bus.publish(Event(
            type=event_type,
            source=self.tool_name,
            payload=payload,
            call_id=self.call_id,
            job_id=self.job_id,
        ))

    def checkpoint(self, payload: dict[str, Any]):
        if self.job_runtime and self.job_id:
            self.job_runtime.update_checkpoint(self.job_id, payload)
        self.emit("tool.job.checkpoint", payload)

    def is_cancelled(self) -> bool:
        if not (self.job_runtime and self.job_id):
            return False
        job = self.job_runtime.get(self.job_id)
        return bool(job and job.cancel_requested)

    def raise_if_cancelled(self):
        if self.is_cancelled():
            raise RuntimeError("job cancelled")


def new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()
