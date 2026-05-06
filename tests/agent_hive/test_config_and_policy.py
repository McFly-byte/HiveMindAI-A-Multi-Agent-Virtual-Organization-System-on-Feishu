from __future__ import annotations

from pathlib import Path

import pytest

from agent_hive.config.env import load_dotenv_if_present
from agent_hive.config.loader import load_runtime_config
from agent_hive.schemas.tool import ToolIntent
from agent_hive.tools.policy import ToolPolicyEngine, ToolPolicyError


ROOT = Path(__file__).resolve().parents[2]


def test_new_runtime_loads_agent_yaml_schema() -> None:
    config = load_runtime_config(ROOT)

    assert "orchestrator" in config.agents
    assert "feishu_tool_agent" in config.agents
    assert config.agents["risk_analysis"].entrypoint == "llm_agent"
    assert config.agents["feishu_tool_agent"].entrypoint == "tool_agent"


def test_business_agents_delegate_feishu_but_can_call_memory() -> None:
    config = load_runtime_config(ROOT)
    risk = config.agents["risk_analysis"]
    policy = ToolPolicyEngine()

    policy.assert_direct_tool_allowed(risk, "memory.search")
    with pytest.raises(ToolPolicyError, match="cannot directly call feishu"):
        policy.assert_direct_tool_allowed(risk, "feishu_bitable_create_field")

    intent = ToolIntent(domain="feishu.bitable", action="add_field")
    assert policy.delegated_agent_for(risk, intent) == "feishu_tool_agent"


def test_agent_hive_loads_dotenv_without_overriding_existing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text(
        "HIVE_ENV_FROM_FILE=from_file\nHIVE_ENV_KEEP=from_file\nQUOTED_VALUE='quoted text'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HIVE_ENV_KEEP", "from_process")

    load_dotenv_if_present(tmp_path)

    assert __import__("os").environ["HIVE_ENV_FROM_FILE"] == "from_file"
    assert __import__("os").environ["HIVE_ENV_KEEP"] == "from_process"
    assert __import__("os").environ["QUOTED_VALUE"] == "quoted text"
