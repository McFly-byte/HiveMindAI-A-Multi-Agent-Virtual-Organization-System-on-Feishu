from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from typing import Any

from agent_runtime.config import RuntimeConfig
from agent_runtime.enums import AgentName, AgentStepType, ErrorType
from agent_runtime.errors import AgentRuntimeError
from agent_runtime.session import AgentSession, AgentStepRecord, RuntimeErrorInfo, ToolCallRecord

from tool_integration.events import EventBus
from tool_integration.loader import env_config, load_dotenv_if_present, resolve_tool_scan_dirs, scan_tool_dirs
from tool_integration.runtime import ToolRuntime
from tool_integration.tools import ToolRegistry


_TOOL_ERROR_TYPE_MAP = {
    "schema_error": ErrorType.INVALID_INPUT,
    "not_found": ErrorType.UNKNOWN,
    "runtime_error": ErrorType.UNKNOWN,
}


def _map_tool_error(raw: str | None) -> ErrorType:
    if not raw:
        return ErrorType.UNKNOWN
    key = str(raw)
    return _TOOL_ERROR_TYPE_MAP.get(key, ErrorType.UNKNOWN)


def _truncate(value: Any, limit: int = 400) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _summarize_invoke_result(out: dict[str, Any]) -> str:
    if not out.get("ok", False):
        return _truncate({"ok": False, "error_type": out.get("error_type"), "message": out.get("message")})
    result = out.get("result")
    return _truncate(result if result is not None else {"ok": True, "tool": out.get("tool")})


def _ensure_act_step(session: AgentSession) -> AgentStepRecord:
    if session.steps and session.steps[-1].step_type == AgentStepType.ACT:
        return session.steps[-1]

    idx = len(session.steps)
    step = AgentStepRecord(
        step_id=f"stp_{uuid4().hex[:12]}",
        step_type=AgentStepType.ACT,
        step_index=idx,
        input_summary="tool integration",
    )
    session.add_step(step)
    return session.steps[-1]


def _agent_config(runtime_config: RuntimeConfig, session: AgentSession):
    agent_name = session.agent_name
    if isinstance(agent_name, AgentName):
        key: AgentName = agent_name
    else:
        key = AgentName(str(agent_name))
    if key not in runtime_config.agents:
        raise AgentRuntimeError(f"No AgentConfig for agent_name={agent_name!r}")
    return runtime_config.agents[key]


def _enforce_tool_policy(tool_name: str, session: AgentSession, runtime_config: RuntimeConfig) -> None:
    cfg = _agent_config(runtime_config, session)
    denied = cfg.tool_policy.denied_tools
    allowed = cfg.tool_policy.allowed_tools

    if tool_name in denied:
        raise AgentRuntimeError(f"Tool {tool_name!r} is denied for agent {cfg.agent_name!r}")

    if allowed and tool_name not in allowed:
        raise AgentRuntimeError(
            f"Tool {tool_name!r} is not allowed for agent {cfg.agent_name!r}. "
            "Align agent.yaml ``tool_policy.allowed_tools`` with registered tool names, "
            "or widen the policy."
        )


class ToolIntegrationExecutor:
    """Bridges ``ToolExecutorProtocol`` to in-package ``ToolRuntime``."""

    def __init__(
        self,
        runtime_config: RuntimeConfig,
        project_root: Path,
        tool_dirs: list[str] | None = None,
        *,
        tool_runtime: ToolRuntime | None = None,
    ) -> None:
        self._runtime_config = runtime_config
        self._project_root = project_root.resolve()
        load_dotenv_if_present(self._project_root)
        self._tool_dirs = resolve_tool_scan_dirs(self._project_root, tool_dirs)

        if tool_runtime is not None:
            self._tool_runtime = tool_runtime
            return

        registry = ToolRegistry()
        event_bus = EventBus()
        scan_tool_dirs(
            registry,
            self._tool_dirs,
            self._project_root,
            event_bus=event_bus,
        )
        self._tool_runtime = ToolRuntime(registry, event_bus, env_config())

    @property
    def tool_runtime(self) -> ToolRuntime:
        return self._tool_runtime

    async def call_tool(
        self, tool_name: str, payload: dict[str, Any], session: AgentSession
    ) -> dict[str, Any]:
        _enforce_tool_policy(tool_name, session, self._runtime_config)

        tool_call_id = f"tcl_{uuid4().hex[:12]}"
        started = datetime.utcnow()
        summary_in = _truncate(payload)

        out = await asyncio.to_thread(self._tool_runtime.invoke, tool_name, payload)

        ended = datetime.utcnow()
        success = bool(out.get("ok", False))

        step = _ensure_act_step(session)
        err_info: RuntimeErrorInfo | None = None
        if not success:
            err_info = RuntimeErrorInfo(
                error_type=_map_tool_error(out.get("error_type")),
                message=str(out.get("message") or "tool call failed"),
                detail=str(out.get("error_type")),
                retryable=False,
            )

        step.tool_calls.append(
            ToolCallRecord(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                input_summary=summary_in,
                output_summary=_summarize_invoke_result(out),
                success=success,
                error=err_info if not success else None,
                started_at=started,
                ended_at=ended,
            )
        )

        return out

    async def shutdown(self) -> None:
        await asyncio.to_thread(self._tool_runtime.shutdown)


def build_tool_integration_executor(
    runtime_config: RuntimeConfig,
    project_root: Path | None = None,
    tool_dirs: list[str] | None = None,
) -> ToolIntegrationExecutor:
    root = Path(project_root).resolve() if project_root else Path.cwd()
    return ToolIntegrationExecutor(runtime_config, root, tool_dirs=tool_dirs)
