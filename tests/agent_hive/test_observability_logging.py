from __future__ import annotations

import asyncio
import logging
import os
import types
from pathlib import Path

from agent_hive.cli import run_cli
from agent_hive.observability.logging import configure_logging, debug_enabled
from agent_hive.tools.providers.feishu import FeishuProvider


ROOT = Path(__file__).resolve().parents[2]


def test_debug_logging_can_be_enabled_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_HIVE_DEBUG", "1")

    configure_logging()

    assert debug_enabled() is True
    assert logging.getLogger("agent_hive").level == logging.DEBUG


def test_cli_debug_flag_sets_agent_hive_logger_level(capsys) -> None:
    exit_code = asyncio.run(run_cli(["--project-root", str(ROOT), "--debug", "list-agents"]))

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "orchestrator" in captured.out
    assert logging.getLogger("agent_hive").level == logging.DEBUG


def test_feishu_im_websocket_flag_is_set_before_tool_registration(monkeypatch) -> None:
    observed: dict[str, str | None] = {}
    module = types.ModuleType("fake_feishu_ws_module")

    def register(registry, event_bus=None) -> None:
        observed["FEISHU_ENABLE_IM_WS"] = os.environ.get("FEISHU_ENABLE_IM_WS")
        if event_bus is not None:
            event_bus.publish({"event_type": "feishu.ws.started", "source": "fake", "payload": {"app_id": "app"}})

    module.register = register  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, module.__name__, module)
    monkeypatch.delenv("FEISHU_ENABLE_IM_WS", raising=False)

    provider = FeishuProvider(modules=[module.__name__])
    provider.enable_im_websocket()
    provider.load()

    assert observed["FEISHU_ENABLE_IM_WS"] == "1"
    assert provider.drain_events()[0]["event_type"] == "feishu.ws.started"
