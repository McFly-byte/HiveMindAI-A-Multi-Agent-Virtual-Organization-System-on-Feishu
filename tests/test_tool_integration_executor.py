from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_runtime.config import RuntimeConfig
from agent_runtime.enums import AgentName, TriggerType
from agent_runtime.errors import AgentRuntimeError
from agent_runtime.loaders import load_agent_config
from agent_runtime.session import AgentSession

from tool_integration.executor import ToolIntegrationExecutor


def _runtime_coordinator() -> RuntimeConfig:
    cfg = load_agent_config(Path("agents/coordinator/agent.yaml"))
    return RuntimeConfig(agents={cfg.agent_name: cfg})


def _runtime_secretary() -> RuntimeConfig:
    cfg = load_agent_config(Path("agents/project_secretary/agent.yaml"))
    return RuntimeConfig(agents={cfg.agent_name: cfg})


def _session(agent: AgentName) -> AgentSession:
    return AgentSession(
        session_id="sid_test",
        run_id="run_test",
        project_id="enterprise_rag",
        agent_name=agent,
        trigger_type=TriggerType.MANUAL,
    )


def test_denied_tool_raises_before_invoke() -> None:
    async def _run() -> None:
        rt = MagicMock()

        exe = ToolIntegrationExecutor(
            _runtime_secretary(),
            Path("."),
            tool_runtime=rt,  # type: ignore[arg-type]
        )
        session = _session(AgentName.PROJECT_SECRETARY)

        with pytest.raises(AgentRuntimeError, match="denied"):
            await exe.call_tool("feishu_bitable_create_record", {}, session)

        rt.invoke.assert_not_called()

    asyncio.run(_run())


def test_allowed_tool_calls_runtime_and_records_step() -> None:
    async def _run() -> None:
        rt = MagicMock()

        rt.invoke.return_value = {
            "ok": True,
            "tool": "trace_tool",
            "call_id": "call_x",
            "async": False,
            "result": {"ok": True, "echo": "x"},
        }

        exe = ToolIntegrationExecutor(
            _runtime_coordinator(),
            Path("."),
            tool_runtime=rt,  # type: ignore[arg-type]
        )
        session = _session(AgentName.COORDINATOR)

        out = await exe.call_tool("trace_tool", {"message": "x"}, session)

        rt.invoke.assert_called_once_with("trace_tool", {"message": "x"})
        assert out["ok"] is True
        assert session.steps[-1].step_type.value == "act"
        assert session.steps[-1].tool_calls[0].tool_name == "trace_tool"
        assert session.steps[-1].tool_calls[0].success is True

    asyncio.run(_run())
