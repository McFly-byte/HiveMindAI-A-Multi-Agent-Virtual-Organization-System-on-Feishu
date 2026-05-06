from pathlib import Path

import pytest

from agent_runtime.enums import AgentName, BaseTableName
from agent_runtime.errors import AgentConfigError
from agent_runtime.loaders import load_agent_config, load_agent_prompt, load_project_manifest


def test_load_agent_config_returns_agent_config() -> None:
    config = load_agent_config(Path("agents/coordinator/agent.yaml"))

    assert config.agent_name == AgentName.COORDINATOR
    assert config.prompt.output_schema_name == "CoordinatorPlan"


def test_coordinator_can_write_with_quality_gate() -> None:
    config = load_agent_config(Path("agents/coordinator/agent.yaml"))

    assert config.write_policy.can_write_base is True
    assert config.write_policy.require_quality_gate is True


def test_project_secretary_cannot_write_base() -> None:
    config = load_agent_config(Path("agents/project_secretary/agent.yaml"))

    assert config.agent_name == AgentName.PROJECT_SECRETARY
    assert config.write_policy.can_write_base is False


def test_load_agent_prompt_includes_agent_and_shared_rules() -> None:
    config = load_agent_config(Path("agents/coordinator/agent.yaml"))
    prompt = load_agent_prompt(config)

    assert "PMO Coordinator Agent" in prompt
    assert "Structured Output Rules" in prompt
    assert "Evidence Rules" in prompt


def test_missing_agent_config_raises_config_error() -> None:
    with pytest.raises(AgentConfigError):
        load_agent_config(Path("agents/missing/agent.yaml"))


def test_load_project_manifest_merges_table_manifest() -> None:
    manifest = load_project_manifest(Path("projects/enterprise_rag"))

    assert manifest.project_id == "enterprise_rag"
    assert BaseTableName.PROJECTS in manifest.tables
    assert manifest.tables[BaseTableName.TASKS].table_id
