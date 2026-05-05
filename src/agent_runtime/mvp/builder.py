from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runtime.enums import AgentName, BaseTableName
from agent_runtime.loaders import load_runtime_config
from agent_runtime.runtime import AgentRuntime

from tool_integration.executor import ToolIntegrationExecutor, build_tool_integration_executor

from agent_runtime.mvp.handlers import MVPAgentRegistry
from agent_runtime.mvp.quality_gate_impl import SimpleRuleQualityGate
from agent_runtime.mvp.trace_sink import LocalJsonlTraceSink


def default_tool_scan_dirs(project_root: Path) -> list[str]:
    """Prefer packaged Feishu tools plus repo-local ``tool_integrations``."""

    root = project_root.resolve()
    segments: list[str] = []
    for seg in ("src/feishu_adapter", "tool_integrations"):
        if (root / seg).is_dir():
            segments.append(seg)
    return segments or ["src/feishu_adapter"]


def build_runtime_with_tool_integration(
    project_root: Path | None = None,
    *,
    tool_dirs: list[str] | None = None,
    trace_dir: Path | None = None,
) -> tuple[AgentRuntime, ToolIntegrationExecutor]:
    """Construct ``AgentRuntime`` with ``ToolIntegrationExecutor`` and MVP registry/handlers."""

    root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
    runtime_config = load_runtime_config(root)
    scan_dirs = tool_dirs if tool_dirs is not None else default_tool_scan_dirs(root)
    executor = build_tool_integration_executor(runtime_config, root, tool_dirs=scan_dirs)

    coord = runtime_config.agents[AgentName.COORDINATOR]
    writable = {BaseTableName(str(t)) for t in coord.write_policy.writable_tables}
    quality_gate = SimpleRuleQualityGate(writable)

    trace_path = trace_dir if trace_dir is not None else (root / runtime_config.local_trace_dir)
    trace_sink = LocalJsonlTraceSink(trace_path)

    registry = MVPAgentRegistry(runtime_config, executor, root, quality_gate)
    runtime = AgentRuntime(runtime_config, registry, executor, quality_gate, trace_sink)
    return runtime, executor
