from __future__ import annotations

from pathlib import Path


DEFAULT_MULTI_AGENT_DIR = Path("multi_agents/default")
DEFAULT_AGENTS_SUBDIR = Path("agents")
DEFAULT_CRON_SUBDIR = Path("cron")
DEFAULT_RUNTIME_DIR = Path("runtime")
DEFAULT_MEMORY_DB = DEFAULT_RUNTIME_DIR / "memory.db"
DEFAULT_TRACE_DIR = DEFAULT_RUNTIME_DIR / "traces"
DEFAULT_RUN_DIR = DEFAULT_RUNTIME_DIR / "runs"


def resolve_project_root(path: Path | str | None = None) -> Path:
    return Path(path).resolve() if path is not None else Path.cwd().resolve()
