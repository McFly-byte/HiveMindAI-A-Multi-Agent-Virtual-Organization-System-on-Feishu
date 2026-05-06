from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agent_hive.config.defaults import DEFAULT_AGENTS_DIR, DEFAULT_MEMORY_DB, DEFAULT_RUN_DIR, DEFAULT_TRACE_DIR
from agent_hive.config.env import load_dotenv_if_present
from agent_hive.config.models import AgentConfig, RuntimeConfig
from agent_hive.config.validator import ConfigValidationError, validate_runtime_config


class AgentConfigError(ValueError):
    pass


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise AgentConfigError(f"configuration file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise AgentConfigError(f"YAML root must be a mapping: {path}")
    return data


def _resolve_path(value: Path, *, project_root: Path, base_dir: Path) -> Path:
    if value.is_absolute():
        return value
    by_base = (base_dir / value).resolve()
    if by_base.exists():
        return by_base
    return (project_root / value).resolve()


def _resolve_agent_paths(config: AgentConfig, *, path: Path, project_root: Path) -> AgentConfig:
    base_dir = path.parent
    update = {
        "workspace_path": _resolve_path(config.workspace_path, project_root=project_root, base_dir=base_dir),
        "prompt": config.prompt.model_copy(
            update={
                "system": _resolve_path(config.prompt.system, project_root=project_root, base_dir=base_dir),
                "shared": [
                    _resolve_path(item, project_root=project_root, base_dir=base_dir)
                    for item in config.prompt.shared
                ],
            }
        ),
    }
    return config.model_copy(update=update)


def load_agent_config(path: Path | str, *, project_root: Path | str | None = None) -> AgentConfig:
    config_path = Path(path).resolve()
    root = Path(project_root).resolve() if project_root is not None else Path.cwd().resolve()
    data = _read_yaml(config_path)
    try:
        config = AgentConfig.model_validate(data)
    except ValidationError as exc:
        raise AgentConfigError(f"invalid agent config: {config_path}") from exc
    return _resolve_agent_paths(config, path=config_path, project_root=root)


def load_all_agent_configs(
    agents_dir: Path | str,
    *,
    project_root: Path | str | None = None,
) -> dict[str, AgentConfig]:
    root = Path(project_root).resolve() if project_root is not None else Path.cwd().resolve()
    base_dir = Path(agents_dir)
    if not base_dir.is_absolute():
        base_dir = (root / base_dir).resolve()
    if not base_dir.exists():
        raise AgentConfigError(f"agents directory not found: {base_dir}")

    agents: dict[str, AgentConfig] = {}
    for config_path in sorted(base_dir.glob("*/agent.yaml")):
        config = load_agent_config(config_path, project_root=root)
        if config.agent_id in agents:
            raise AgentConfigError(f"duplicate agent_id {config.agent_id!r}: {config_path}")
        agents[config.agent_id] = config
    if not agents:
        raise AgentConfigError(f"no agent.yaml files found under {base_dir}")
    return agents


def load_agent_prompt(agent_config: AgentConfig) -> str:
    paths = [agent_config.prompt.system, *agent_config.prompt.shared]
    chunks: list[str] = []
    for path in paths:
        if not path.exists():
            raise AgentConfigError(f"prompt file not found: {path}")
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(chunks)


def load_runtime_config(
    project_root: Path | str | None = None,
    *,
    agents_dir: Path | str = DEFAULT_AGENTS_DIR,
) -> RuntimeConfig:
    root = Path(project_root).resolve() if project_root is not None else Path.cwd().resolve()
    load_dotenv_if_present(root)
    agents_root = Path(agents_dir)
    if not agents_root.is_absolute():
        agents_root = (root / agents_root).resolve()
    agents = load_all_agent_configs(agents_root, project_root=root)
    config = RuntimeConfig(
        project_root=root,
        agents_root=agents_root,
        runtime_dir=(root / "runtime").resolve(),
        memory_db_path=(root / DEFAULT_MEMORY_DB).resolve(),
        trace_dir=(root / DEFAULT_TRACE_DIR).resolve(),
        run_dir=(root / DEFAULT_RUN_DIR).resolve(),
        agents=agents,
    )
    try:
        validate_runtime_config(config)
    except ConfigValidationError as exc:
        raise AgentConfigError(str(exc)) from exc
    return config
