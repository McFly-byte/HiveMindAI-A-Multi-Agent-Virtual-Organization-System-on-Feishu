from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agent_runtime.config import AgentConfig, RuntimeConfig
from agent_runtime.enums import AgentName
from agent_runtime.errors import AgentConfigError
from agent_runtime.project_state import ProjectManifest


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise AgentConfigError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise AgentConfigError(f"YAML root must be a mapping: {path}")
    return data


def _resolve_path(path: Path, project_root: Path) -> Path:
    return path if path.is_absolute() else project_root / path


def load_agent_config(path: Path) -> AgentConfig:
    """Load and validate a single agent.yaml file."""

    config_path = path if path.is_absolute() else Path.cwd() / path
    data = _read_yaml(config_path)
    try:
        return AgentConfig.model_validate(data)
    except ValidationError as exc:
        raise AgentConfigError(f"Invalid agent config: {path}") from exc


def load_all_agent_configs(agents_dir: Path) -> dict[AgentName, AgentConfig]:
    """Load every agents/*/agent.yaml under a project root."""

    base_dir = agents_dir if agents_dir.is_absolute() else Path.cwd() / agents_dir
    if not base_dir.exists():
        raise AgentConfigError(f"Agents directory not found: {agents_dir}")
    configs: dict[AgentName, AgentConfig] = {}
    for config_path in sorted(base_dir.glob("*/agent.yaml")):
        config = load_agent_config(config_path)
        configs[config.agent_name] = config
    return configs


def load_agent_prompt(agent_config: AgentConfig) -> str:
    """Load an agent prompt plus its shared prompt fragments."""

    project_root = Path.cwd()
    prompt_paths = [agent_config.prompt.agent_md_path, *agent_config.prompt.shared_prompt_paths]
    contents: list[str] = []
    for prompt_path in prompt_paths:
        resolved = _resolve_path(prompt_path, project_root)
        if not resolved.exists():
            raise AgentConfigError(f"Prompt file not found: {prompt_path}")
        contents.append(resolved.read_text(encoding="utf-8"))
    return "\n\n".join(contents)


def load_project_manifest(project_dir: Path) -> ProjectManifest:
    """Load project_state.yaml and table_manifest.yaml into ProjectManifest."""

    base_dir = project_dir if project_dir.is_absolute() else Path.cwd() / project_dir
    if not base_dir.exists():
        raise AgentConfigError(f"Project directory not found: {project_dir}")

    project_data = _read_yaml(base_dir / "project_state.yaml")
    table_manifest = _read_yaml(base_dir / "table_manifest.yaml")
    raw_tables = table_manifest.get("tables", {})
    if not isinstance(raw_tables, dict):
        raise AgentConfigError("table_manifest.yaml tables must be a mapping")

    tables: dict[str, dict[str, Any]] = {}
    for table_name, table_data in raw_tables.items():
        if not isinstance(table_data, dict):
            raise AgentConfigError(f"Table manifest must be a mapping: {table_name}")
        tables[table_name] = {"table_name": table_name, **table_data}

    project_data["tables"] = tables
    try:
        return ProjectManifest.model_validate(project_data)
    except ValidationError as exc:
        raise AgentConfigError(f"Invalid project manifest: {project_dir}") from exc


def load_runtime_config(project_root: Path) -> RuntimeConfig:
    """Load all agent configs into a RuntimeConfig."""

    root = project_root if project_root.is_absolute() else Path.cwd() / project_root
    agents = load_all_agent_configs(root / "agents")
    return RuntimeConfig(agents=agents)
