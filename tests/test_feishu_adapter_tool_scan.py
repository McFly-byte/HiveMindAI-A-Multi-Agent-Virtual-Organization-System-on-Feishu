"""Integration: real ``src/feishu_adapter`` modules load via ``scan_tool_dirs``.

``tests/conftest.py`` prepends ``<repo>/src`` to ``sys.path`` so ``pytest`` needs no
manual ``PYTHONPATH``. Project layout expects ``HIVEMIND_TOOL_SCAN_DIRS=src/feishu_adapter``
in ``.env`` (root), not bare ``feishu_adapter``, unless that directory exists at root."""

from __future__ import annotations

from pathlib import Path

import pytest

from tool_integration.events import EventBus
from tool_integration.loader import (
    TOOL_SCAN_DIRS_ENV,
    load_dotenv_if_present,
    resolve_tool_scan_dirs,
    scan_tool_dirs,
)
from tool_integration.tools import ToolRegistry

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC_FEISHU_ADAPTER = Path("src/feishu_adapter")


def test_src_feishu_adapter_package_on_disk() -> None:
    assert (_PROJECT_ROOT / _SRC_FEISHU_ADAPTER).is_dir()


def test_scan_src_feishu_adapter_registers_drive_tool() -> None:
    rel = _SRC_FEISHU_ADAPTER.as_posix()
    reg = ToolRegistry()
    scan_tool_dirs(reg, [rel], _PROJECT_ROOT, event_bus=EventBus())
    assert "feishu_drive_list_folder" in reg.tools
    assert len(reg.tools) >= 8


def test_scan_src_feishu_adapter_includes_eval_and_bitable() -> None:
    rel = _SRC_FEISHU_ADAPTER.as_posix()
    reg = ToolRegistry()
    scan_tool_dirs(reg, [rel], _PROJECT_ROOT, event_bus=EventBus())
    assert "feishu_bitable_parse_url" in reg.tools
    assert "util_python_eval" in reg.tools


@pytest.mark.parametrize(
    "env_value,expected_segments",
    [
        ("src/feishu_adapter", ["src/feishu_adapter"]),
        ("feishu_adapter,tool_integrations", ["feishu_adapter", "tool_integrations"]),
    ],
)
def test_resolve_tool_scan_dirs_matches_param(
    monkeypatch: pytest.MonkeyPatch, env_value: str, expected_segments: list[str]
) -> None:
    monkeypatch.setenv(TOOL_SCAN_DIRS_ENV, env_value)
    assert resolve_tool_scan_dirs(_PROJECT_ROOT, None) == expected_segments


def test_dotenv_resolve_matches_repo_default_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the real ``.env`` at project root (no override): must list ``src/feishu_adapter``."""
    monkeypatch.delenv(TOOL_SCAN_DIRS_ENV, raising=False)
    load_dotenv_if_present(_PROJECT_ROOT)
    dirs = resolve_tool_scan_dirs(_PROJECT_ROOT, None)
    assert dirs == ["src/feishu_adapter"], (
        "Set HIVEMIND_TOOL_SCAN_DIRS=src/feishu_adapter in .env "
        "(feishu code lives under src/, not repo-root feishu_adapter)."
    )


def test_full_chain_dotenv_directories_scan_registers_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOOL_SCAN_DIRS_ENV, raising=False)
    load_dotenv_if_present(_PROJECT_ROOT)
    dirs = resolve_tool_scan_dirs(_PROJECT_ROOT, None)
    reg = ToolRegistry()
    scan_tool_dirs(reg, dirs, _PROJECT_ROOT, event_bus=EventBus())
    assert len(reg.tools) >= 8
    assert "feishu_drive_list_folder" in reg.tools
