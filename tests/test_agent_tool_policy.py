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


def _session(agent: AgentName) -> AgentSession:
    return AgentSession(
        session_id="sid_policy",
        run_id="run_policy",
        project_id="enterprise_rag",
        agent_name=agent,
        trigger_type=TriggerType.MANUAL,
    )


def test_disallowed_tool_not_invoked() -> None:
    async def _run() -> None:
        root = Path(__file__).resolve().parents[1]
        sec = load_agent_config(root / "agents" / "project_secretary" / "agent.yaml")
        cfg = RuntimeConfig(agents={sec.agent_name: sec})
        rt = MagicMock()
        exe = ToolIntegrationExecutor(cfg, root, tool_runtime=rt)  # type: ignore[arg-type]
        session = _session(AgentName.PROJECT_SECRETARY)

        with pytest.raises(AgentRuntimeError, match="not allowed"):
            await exe.call_tool(
                "feishu_bitable_list_tables",
                {"app_token": "dummy"},
                session,
            )

        rt.invoke.assert_not_called()

    asyncio.run(_run())
