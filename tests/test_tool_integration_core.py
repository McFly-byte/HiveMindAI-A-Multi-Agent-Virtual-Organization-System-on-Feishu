"""Exercise ``tool_integration`` without Feishu: events, schema, jobs, ToolRuntime."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from agent_runtime.config import RuntimeConfig
from agent_runtime.enums import AgentName, TriggerType
from agent_runtime.errors import AgentRuntimeError
from agent_runtime.loaders import load_agent_config
from agent_runtime.session import AgentSession

from tool_integration.events import (
    EventBus,
    ToolEventScope,
    ToolIntegrationEvent,
    ToolIntegrationEventType,
)
from tool_integration.executor import ToolIntegrationExecutor
from tool_integration.jobs import JobRuntime
from tool_integration.loader import (
    TOOL_SCAN_DIRS_ENV,
    load_dotenv_if_present,
    resolve_tool_scan_dirs,
    scan_tool_dirs,
)
from tool_integration.runtime import ToolRuntime
from tool_integration.schema import SchemaError, validate_schema
from tool_integration.tools import ToolContext, ToolRegistry, ToolSpec

# -----------------------------------------------------------------------------
# Minimal JSON-schema fixtures for ToolSpec (subset parser in schema.py).


def _object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    sch: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        sch["required"] = required
    return sch


# -----------------------------------------------------------------------------
# Event bus & ToolIntegrationEvent model


def test_event_bus_subscribes_filtered_types() -> None:
    bus = EventBus()
    bus.subscribe("a", ["tool.call.started", "tool.call.finished"])

    bus.publish(
        ToolIntegrationEvent(
            event_type=ToolIntegrationEventType.TOOL_CALL_STARTED,
            source="demo",
            payload={},
            call_id="c1",
        )
    )
    bus.publish(
        ToolIntegrationEvent(
            event_type=ToolIntegrationEventType.TOOL_JOB_FINISHED,
            source="demo",
            payload={},
            job_id="j1",
        )
    )

    drained = bus.drain("a")
    assert len(drained) == 1
    assert drained[0].event_type == ToolIntegrationEventType.TOOL_CALL_STARTED


def test_event_bus_star_subscription_receives_any_type() -> None:
    bus = EventBus()
    bus.subscribe("w", ["*"])

    bus.publish(
        ToolIntegrationEvent(
            event_type="feishu.custom.domain",
            source="src",
            payload={"x": 1},
        )
    )

    events = bus.drain("w")
    assert len(events) == 1
    assert events[0].event_type == "feishu.custom.domain"


def test_tool_integration_event_model_dump_strings_enum_type() -> None:
    ev = ToolIntegrationEvent(
        event_type=ToolIntegrationEventType.TOOL_CALL_FINISHED,
        source="echo",
        call_id="c9",
        scope=ToolEventScope(project_id="p1", run_id="r1"),
    )
    dumped = ev.model_dump(mode="json")
    assert dumped["event_type"] == "tool.call.finished"
    assert dumped["scope"]["project_id"] == "p1"


# -----------------------------------------------------------------------------
# Schema validation subset


def test_validate_schema_rejects_missing_required_key() -> None:
    schema = _object_schema({"n": {"type": "integer"}}, required=["n"])
    with pytest.raises(SchemaError, match=r"\.n"):
        validate_schema(schema, {}, path="$")


def test_validate_schema_accepts_union_primitive_types() -> None:
    schema = {"type": ["string", "null"]}
    validate_schema(schema, None)
    validate_schema(schema, "ok")


# -----------------------------------------------------------------------------
# JobRuntime (no threads)


def test_job_runtime_checkpoint_sets_progress_and_event() -> None:
    jr = JobRuntime()
    job = jr.create("t_slow", "call_x", {}, "background")
    jr.update_checkpoint(job.job_id, {"progress": 0.42, "note": "mid"})

    got = jr.get(job.job_id)
    assert got is not None
    assert got.progress == pytest.approx(0.42)
    assert len(got.events) == 1
    assert got.events[0]["type"] == "tool.job.checkpoint"


def test_job_runtime_switch_to_background_updates_view_mode() -> None:
    jr = JobRuntime()
    job = jr.create("t", "c", {}, "foreground")
    out = jr.switch_to_background(job.job_id, "test_reason")
    assert out["ok"] is True
    assert jr.get(job.job_id).view_mode == "background"


# -----------------------------------------------------------------------------
# ToolRegistry


def test_registry_rejects_duplicate_tool_name() -> None:
    reg = ToolRegistry()
    spec = ToolSpec(
        name="dup",
        description="",
        input_schema=_object_schema({}),
        output_schema=_object_schema({"r": {"type": "boolean"}}, required=["r"]),
    )

    def f(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return {"r": True}

    reg.add(spec, f)
    with pytest.raises(ValueError, match="duplicated"):
        reg.add(spec, f)


# -----------------------------------------------------------------------------
# ToolRuntime (sync / async) — always shutdown executor


@pytest.fixture
def echo_sync_runtime() -> Any:
    reg = ToolRegistry()
    bus = EventBus()
    spec = ToolSpec(
        name="echo",
        description="return x",
        input_schema=_object_schema({"x": {"type": "integer"}}, required=["x"]),
        output_schema=_object_schema({"out": {"type": "integer"}}, required=["out"]),
        mode="sync",
    )

    @reg.register(spec)
    def echo_tool(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        ctx.emit("domain.probe", {"k": True})
        return {"out": int(args["x"])}

    rt = ToolRuntime(reg, bus, config={"tenant": "t1"})
    try:
        yield rt, bus
    finally:
        rt.shutdown()


@pytest.fixture
def async_checkpoint_runtime() -> Any:
    reg = ToolRegistry()
    bus = EventBus()
    spec = ToolSpec(
        name="checkpoint_tool",
        description="async with checkpoint",
        input_schema=_object_schema({}),
        output_schema=_object_schema({"done": {"type": "boolean"}}, required=["done"]),
        mode="async",
    )

    @reg.register(spec)
    def ck_tool(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        ctx.checkpoint({"progress": 0.2})
        time.sleep(0.03)
        return {"done": True}

    rt = ToolRuntime(reg, bus)
    try:
        yield rt, bus
    finally:
        rt.shutdown()


def test_tool_runtime_sync_happy_path_and_custom_emit(echo_sync_runtime: Any) -> None:
    rt, bus = echo_sync_runtime
    bus.subscribe("sub", ["*"])

    out = rt.invoke("echo", {"x": 7})

    assert out["ok"] is True
    assert out["result"] == {"out": 7}

    drained = bus.drain("sub")
    types_in_order = [e.event_type for e in drained]
    assert ToolIntegrationEventType.TOOL_CALL_STARTED.value in types_in_order
    assert "domain.probe" in types_in_order
    assert ToolIntegrationEventType.TOOL_CALL_FINISHED.value in types_in_order


def test_tool_runtime_unknown_tool_returns_error_without_started_event() -> None:
    reg = ToolRegistry()
    bus = EventBus()
    bus.subscribe("sub", ["*", "tool.call.started"])

    rt = ToolRuntime(reg, bus)
    try:
        out = rt.invoke("missing_tool", {})
    finally:
        rt.shutdown()

    assert out["ok"] is False
    assert out["error_type"] == "not_found"
    assert bus.drain("sub")[0].event_type == ToolIntegrationEventType.TOOL_CALL_FAILED.value


def test_tool_runtime_input_schema_error_after_started() -> None:
    reg = ToolRegistry()
    bus = EventBus()
    spec = ToolSpec(
        name="needs_int",
        description="",
        input_schema=_object_schema({"x": {"type": "integer"}}, required=["x"]),
        output_schema=_object_schema({"ok": {"type": "boolean"}}, required=["ok"]),
        mode="sync",
    )

    @reg.register(spec)
    def _(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return {"ok": True}

    rt = ToolRuntime(reg, bus)
    bus.subscribe("t", ["*"])
    try:
        out = rt.invoke("needs_int", {"x": "not-int"})
    finally:
        rt.shutdown()

    assert out["ok"] is False
    assert out["error_type"] == "schema_error"
    tail = bus.drain("t")
    assert tail[0].event_type == ToolIntegrationEventType.TOOL_CALL_STARTED
    assert tail[-1].event_type == ToolIntegrationEventType.TOOL_CALL_FAILED.value


def test_tool_runtime_async_job_finishes(async_checkpoint_runtime: Any) -> None:
    rt, bus = async_checkpoint_runtime
    bus.subscribe("w", ["*"])

    out = rt.invoke("checkpoint_tool", {})
    assert out["ok"] is True
    assert out["async"] is True
    job_id = out["job_id"]

    wait_res = rt.jobs.wait(job_id, timeout_seconds=5.0)
    assert wait_res["ok"] is True
    assert wait_res["timed_out"] is False
    job_dict = wait_res["job"]
    assert job_dict["state"] == "succeeded"
    assert job_dict["result"] == {"done": True}

    drained = bus.drain("w")
    event_types = {e.event_type for e in drained}
    assert ToolIntegrationEventType.TOOL_JOB_STARTED.value in event_types
    assert ToolIntegrationEventType.TOOL_JOB_CHECKPOINT.value in event_types
    assert ToolIntegrationEventType.TOOL_JOB_FINISHED.value in event_types


# -----------------------------------------------------------------------------
# Loader: HIVEMIND_TOOL_SCAN_DIRS (e.g. ``feishu_adapter`` under project root)


def test_resolve_tool_scan_dirs_default_when_env_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(TOOL_SCAN_DIRS_ENV, raising=False)
    assert resolve_tool_scan_dirs(tmp_path, None) == ["tool_integrations"]


def test_resolve_tool_scan_dirs_single_feishu_adapter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Matches typical ``.env``: ``HIVEMIND_TOOL_SCAN_DIRS=feishu_adapter``."""
    monkeypatch.setenv(TOOL_SCAN_DIRS_ENV, "feishu_adapter")
    assert resolve_tool_scan_dirs(tmp_path, None) == ["feishu_adapter"]


def test_resolve_tool_scan_dirs_feishu_adapter_trimmed_and_multi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(TOOL_SCAN_DIRS_ENV, " feishu_adapter , tool_integrations ")
    assert resolve_tool_scan_dirs(tmp_path, None) == ["feishu_adapter", "tool_integrations"]


def test_resolve_tool_scan_dirs_override_ignores_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(TOOL_SCAN_DIRS_ENV, "feishu_adapter")
    assert resolve_tool_scan_dirs(tmp_path, ["custom_only"]) == ["custom_only"]


def test_load_dotenv_if_present_sets_os_environ(tmp_path: Path) -> None:
    key = "_HIVE_DOTENV_LOADER_TEST"
    old = os.environ.pop(key, None)
    try:
        (tmp_path / ".env").write_text(f'{key}=from_dotenv\n', encoding="utf-8")
        load_dotenv_if_present(tmp_path)
        assert os.environ.get(key) == "from_dotenv"
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def test_load_dotenv_then_resolve_reads_hivemind_tool_scan_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``.env`` + resolve reproduces IDE/local config without passing ``tool_dirs``."""
    monkeypatch.delenv(TOOL_SCAN_DIRS_ENV, raising=False)
    (tmp_path / ".env").write_text(
        f"{TOOL_SCAN_DIRS_ENV}=feishu_adapter,tool_integrations\n",
        encoding="utf-8",
    )
    load_dotenv_if_present(tmp_path)
    assert resolve_tool_scan_dirs(tmp_path, None) == ["feishu_adapter", "tool_integrations"]


def test_resolve_nested_src_feishu_adapter_supported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If tools stay under ``src/``, env can still point at ``src/feishu_adapter``."""
    monkeypatch.setenv(TOOL_SCAN_DIRS_ENV, "src/feishu_adapter")
    assert resolve_tool_scan_dirs(tmp_path, None) == ["src/feishu_adapter"]


STUB_FEISHU_REGISTER_PY = '''from __future__ import annotations
from typing import Any
from tool_integration.tools import ToolContext, ToolRegistry, ToolSpec


def register(registry: ToolRegistry, **kwargs: Any) -> str:
    @registry.register(
        ToolSpec(
            name="stub_feishu_ping",
            description="test stub under feishu_adapter directory",
            input_schema={"type": "object", "properties": {}},
            output_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
            mode="sync",
        )
    )
    def _ping(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return {"ok": True}

    return "feishu_stub"
'''


def test_scan_tool_dirs_under_feishu_adapter_path_registers_stub(tmp_path: Path) -> None:
    """Proves layout ``<project>/feishu_adapter/*.py`` + ``register()`` works."""
    adapter = tmp_path / "feishu_adapter"
    adapter.mkdir(parents=True)
    (adapter / "stub_ping.py").write_text(STUB_FEISHU_REGISTER_PY, encoding="utf-8")

    reg = ToolRegistry()
    scan_tool_dirs(reg, ["feishu_adapter"], tmp_path, event_bus=EventBus())
    assert "stub_feishu_ping" in reg.tools

    rt = ToolRuntime(reg, EventBus(), config={})
    try:
        out = rt.invoke("stub_feishu_ping", {})
        assert out.get("ok") is True
        assert out.get("result") == {"ok": True}
    finally:
        rt.shutdown()


# -----------------------------------------------------------------------------
# Executor edge (AgentConfig missing)


def test_executor_raises_when_session_agent_not_in_runtime_config() -> None:
    import asyncio

    async def _run() -> None:
        sec = load_agent_config(Path("agents/project_secretary/agent.yaml"))
        runtime_config = RuntimeConfig(agents={sec.agent_name: sec})
        rt = ToolRuntime(ToolRegistry(), EventBus())

        exe = ToolIntegrationExecutor(runtime_config, Path("."), tool_runtime=rt)  # type: ignore[arg-type]
        session = AgentSession(
            session_id="s",
            run_id="r",
            project_id="p",
            agent_name=AgentName.COORDINATOR,
            trigger_type=TriggerType.MANUAL,
        )

        with pytest.raises(AgentRuntimeError, match="No AgentConfig"):
            await exe.call_tool("anything", {}, session)

        rt.shutdown()

    asyncio.run(_run())
