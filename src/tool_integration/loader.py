from __future__ import annotations

import importlib.util
import inspect
import os
import sys
from pathlib import Path
from typing import Any

from tool_integration.tools import ToolRegistry

TOOL_SCAN_DIRS_ENV = "HIVEMIND_TOOL_SCAN_DIRS"


def load_dotenv_if_present(base_dir: Path, *, override: bool = False) -> None:
    """Load ``base_dir / ".env`` into ``os.environ`` (KEY=VALUE lines, UTF-8).

    Does not require ``python-dotenv``. Existing process env wins unless ``override`` is True.
    """
    path = (base_dir / ".env").resolve()
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, _, value = s.partition("=")
        key = key.strip()
        if not key:
            continue
        raw = value.strip()
        if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
            raw = raw[1:-1]
        if override or key not in os.environ:
            os.environ[key] = raw


def resolve_tool_scan_dirs(base_dir: Path, override: list[str] | None = None) -> list[str]:
    """Directories under ``base_dir`` to pass to :func:`scan_tool_dirs`.

    Reads comma-separated relative paths from env ``HIVEMIND_TOOL_SCAN_DIRS`` (after
    :func:`load_dotenv_if_present`). Default: ``["tool_integrations"]``.

    Typical segments: ``feishu_adapter`` (next to project root) or ``src/feishu_adapter``
    when implementations live under ``src``. Each segment is resolved as
    ``<project_root> / segment`` in :func:`scan_tool_dirs`.
    """
    if override is not None:
        return list(override)
    raw = os.environ.get(TOOL_SCAN_DIRS_ENV, "").strip()
    if not raw:
        return ["tool_integrations"]
    return [segment.strip() for segment in raw.split(",") if segment.strip()]


def scan_tool_dirs(registry: ToolRegistry, tool_dirs: list[str], base_dir: Path, **register_kwargs: Any):
    """Import ``tool_dirs`` under ``base_dir`` and call ``register(registry, **kwargs)`` on each ``*.py``."""
    for raw_dir in tool_dirs:
        directory = (base_dir / raw_dir).resolve()
        if not directory.exists():
            continue
        for py in sorted(directory.glob("*.py")):
            if py.name.startswith("_"):
                continue
            module_name = f"tool_integration_user_tool_{py.stem}_{abs(hash(str(py))) & 0xfffffff}"
            spec = importlib.util.spec_from_file_location(module_name, py)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            if hasattr(module, "register"):
                fn = module.register
                before = set(registry.tools.keys())
                try:
                    sig = inspect.signature(fn)
                    accepted = {k: v for k, v in register_kwargs.items() if k in sig.parameters}
                except Exception:
                    accepted = {}
                ret = fn(registry, **accepted)
                added = sorted(set(registry.tools.keys()) - before)
                if added:
                    if isinstance(ret, str) and ret.strip():
                        toolsets = [ret.strip()]
                    elif isinstance(ret, (list, tuple)):
                        toolsets = [str(x).strip() for x in ret if str(x).strip()]
                    else:
                        toolsets = [py.stem]
                    for toolset in toolsets:
                        registry.add_tools_to_toolset(toolset, added)


def env_config() -> dict[str, Any]:
    return dict(os.environ)
