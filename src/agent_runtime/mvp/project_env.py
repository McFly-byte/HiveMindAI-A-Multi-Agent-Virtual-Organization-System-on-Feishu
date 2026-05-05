from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def expand_env_in_str(value: str) -> str:
    """Replace ``${VAR}`` segments with ``os.environ.get(VAR, "")``."""

    def repl(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), "")

    return _ENV_PATTERN.sub(repl, value)


def expand_env_value(value: Any) -> Any:
    if isinstance(value, str):
        return expand_env_in_str(value)
    if isinstance(value, dict):
        return {k: expand_env_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env_value(v) for v in value]
    return value


def collect_unresolved_placeholders(value: Any, out: set[str] | None = None) -> set[str]:
    """Return env var names still referenced as ``${NAME}`` after expansion (empty means missing)."""

    missing: set[str] = out or set()

    def scan_str(s: str) -> None:
        for m in _ENV_PATTERN.finditer(s):
            name = m.group(1)
            if not os.environ.get(name):
                missing.add(name)

    if isinstance(value, str):
        scan_str(value)
    elif isinstance(value, dict):
        for v in value.values():
            collect_unresolved_placeholders(v, missing)
    elif isinstance(value, list):
        for v in value:
            collect_unresolved_placeholders(v, missing)
    return missing


def feishu_auth_env_missing() -> list[str]:
    missing: list[str] = []
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        if not os.environ.get(key, "").strip():
            missing.append(key)
    return missing


def feishu_demo_chain_env_missing(project_root: Path) -> list[str]:
    """Vars required for ``run_mvp_demo_chain`` against ``projects/enterprise_rag``."""

    missing = feishu_auth_env_missing()
    from agent_runtime.errors import AgentConfigError
    from agent_runtime.loaders import load_project_manifest

    try:
        manifest = load_project_manifest(project_root / "projects" / "enterprise_rag")
    except AgentConfigError:
        return sorted(set(missing))

    raw = manifest.model_dump(mode="json")
    names = collect_unresolved_placeholders(raw)
    missing.extend(sorted(names))

    expanded = expand_env_value(raw)
    if not str(expanded.get("base_app_token", "")).strip():
        missing.append("FEISHU_BASE_APP_TOKEN")

    raw_tables = raw.get("tables") or {}
    exp_tables = expanded.get("tables") or {}
    for tkey, trow in exp_tables.items():
        tid = str((trow or {}).get("table_id", "")).strip()
        if tid:
            continue
        raw_row = raw_tables.get(tkey) or raw_tables.get(str(tkey)) or {}
        raw_tid = raw_row.get("table_id", "")
        if isinstance(raw_tid, str):
            found = sorted({m.group(1) for m in _ENV_PATTERN.finditer(raw_tid)})
            if found:
                missing.extend(found)
            elif not raw_tid.strip():
                missing.append(f"FEISHU_TABLE_* (table {tkey!r} table_id is empty)")
        else:
            missing.append(f"FEISHU_TABLE_* (table {tkey!r} table_id is missing)")

    return sorted(set(missing))
