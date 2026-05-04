from __future__ import annotations

import importlib.util
import os
import sys
import inspect
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .tools import ToolRegistry


def load_spec(path: str | Path) -> dict[str, Any]:
    spec_path = Path(path).resolve()
    if not spec_path.exists():
        raise FileNotFoundError(spec_path)
    module_name = f"tooltest_spec_{spec_path.stem.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, spec_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load spec: {spec_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, "SPEC"):
        raise RuntimeError(f"spec file must define SPEC: {spec_path}")
    return module.SPEC


def load_dotenv_for_spec(spec: dict[str, Any]):
    dotenv_path = spec.get("dotenv", ".env")
    load_dotenv(dotenv_path)


def scan_tool_dirs(registry: ToolRegistry, tool_dirs: list[str], base_dir: Path, **register_kwargs: Any):
    for raw_dir in tool_dirs:
        directory = (base_dir / raw_dir).resolve()
        if not directory.exists():
            continue
        for py in sorted(directory.glob("*.py")):
            if py.name.startswith("_"):
                continue
            module_name = f"tooltest_user_tool_{py.stem}_{abs(hash(str(py))) & 0xfffffff}"
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
