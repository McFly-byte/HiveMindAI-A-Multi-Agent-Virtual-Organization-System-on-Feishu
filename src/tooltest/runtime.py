from __future__ import annotations

import traceback
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .events import Event, EventBus
from .jobs import JobRuntime, JobRecord
from .schema import SchemaError, validate_schema
from .tools import ToolRegistry, Tool, ToolContext, ToolSpec, SwitchPolicy, new_call_id


class ToolRuntime:
    def __init__(self, registry: ToolRegistry, event_bus: EventBus, config: dict[str, Any] | None = None):
        self.registry = registry
        self.event_bus = event_bus
        self.config = config or {}
        self.executor = ThreadPoolExecutor(max_workers=8)
        self.jobs = JobRuntime()
        self.call_count = 0
        self.last_error: str | None = None

    def invoke(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        call_id = new_call_id()
        try:
            tool = self.registry.get(tool_name)
        except Exception as e:
            return self._error(tool_name, call_id, "not_found", str(e))

        self.event_bus.publish(Event(
            type="tool.call.started",
            source=tool_name,
            call_id=call_id,
            payload={"args": args, "mode": tool.spec.mode, "kind": tool.spec.kind},
        ))

        try:
            validate_schema(tool.spec.input_schema, args)
        except SchemaError as e:
            return self._error(tool_name, call_id, "schema_error", str(e))

        if tool.spec.mode == "sync":
            return self._invoke_sync(tool, args, call_id)
        if tool.spec.mode == "async":
            return self._invoke_async(tool, args, call_id, view_mode="background")
        if tool.spec.mode == "switchable":
            return self._invoke_switchable(tool, args, call_id)
        return self._error(tool_name, call_id, "runtime_error", f"unknown tool mode: {tool.spec.mode}")

    def _invoke_sync(self, tool: Tool, args: dict[str, Any], call_id: str) -> dict[str, Any]:
        ctx = ToolContext(tool.spec.name, self.event_bus, call_id, config=self.config)
        try:
            result = tool.func(args, ctx)
            validate_schema(tool.spec.output_schema, result)
            out = {"ok": True, "tool": tool.spec.name, "call_id": call_id, "async": False, "result": result}
            self.event_bus.publish(Event(type="tool.call.finished", source=tool.spec.name, call_id=call_id, payload=out))
            return out
        except Exception as e:
            return self._error(tool.spec.name, call_id, "runtime_error", str(e), traceback.format_exc())

    def _invoke_async(self, tool: Tool, args: dict[str, Any], call_id: str, view_mode: str = "background") -> dict[str, Any]:
        job = self.jobs.create(tool.spec.name, call_id, args, view_mode=view_mode)  # type: ignore[arg-type]
        self.event_bus.publish(Event(type="tool.job.started", source=tool.spec.name, call_id=call_id, job_id=job.job_id, payload={"tool": tool.spec.name, "args": args, "view_mode": view_mode}))
        future = self.executor.submit(self._run_job, tool, job)
        job.future = future
        return {"ok": True, "tool": tool.spec.name, "call_id": call_id, "async": True, "job_id": job.job_id, "status": "running", "view_mode": view_mode}

    def _invoke_switchable(self, tool: Tool, args: dict[str, Any], call_id: str) -> dict[str, Any]:
        policy = tool.spec.switch_policy or SwitchPolicy()
        job = self.jobs.create(tool.spec.name, call_id, args, view_mode="foreground")
        self.event_bus.publish(Event(type="tool.job.started", source=tool.spec.name, call_id=call_id, job_id=job.job_id, payload={"tool": tool.spec.name, "args": args, "view_mode": "foreground", "switchable": True}))
        future = self.executor.submit(self._run_job, tool, job)
        job.future = future
        self.event_bus.publish(Event(type="tool.job.foreground_wait_started", source=tool.spec.name, call_id=call_id, job_id=job.job_id, payload={"timeout_seconds": policy.foreground_timeout_seconds}))

        deadline = time.time() + policy.foreground_timeout_seconds
        while time.time() < deadline:
            if job.state in ("succeeded", "failed", "cancelled"):
                if job.state == "succeeded":
                    return {"ok": True, "tool": tool.spec.name, "call_id": call_id, "async": False, "result": job.result, "job_id": job.job_id, "completed_as_foreground": True}
                return self._error(tool.spec.name, call_id, job.state, job.error or job.state, job.traceback, extra={"job_id": job.job_id})
            if job.view_mode == "background":
                return self._switched_result(tool.spec.name, call_id, job, "user_or_agent_requested")
            time.sleep(max(0.02, min(0.1, policy.check_timeout_seconds)))

        self.jobs.switch_to_background(job.job_id, "foreground_timeout")
        self.event_bus.publish(Event(type="tool.job.switched_to_background", source=tool.spec.name, call_id=call_id, job_id=job.job_id, payload={"reason": "foreground_timeout"}))
        return self._switched_result(tool.spec.name, call_id, job, "foreground_timeout")

    def _switched_result(self, tool_name: str, call_id: str, job: JobRecord, reason: str) -> dict[str, Any]:
        return {"ok": True, "tool": tool_name, "call_id": call_id, "async": True, "switched_to_async": True, "job_id": job.job_id, "status": job.state, "view_mode": job.view_mode, "reason": reason}

    def _run_job(self, tool: Tool, job: JobRecord):
        self.jobs.set_running(job.job_id)
        ctx = ToolContext(tool.spec.name, self.event_bus, job.call_id, job_runtime=self.jobs, job_id=job.job_id, config=self.config)
        try:
            if job.cancel_requested:
                raise RuntimeError("job cancelled")
            result = tool.func(job.args, ctx)
            validate_schema(tool.spec.output_schema, result)
            self.jobs.set_succeeded(job.job_id, result)
            self.event_bus.publish(Event(type="tool.job.finished", source=tool.spec.name, call_id=job.call_id, job_id=job.job_id, payload={"result": result}))
        except Exception as e:
            self.jobs.set_failed(job.job_id, str(e), traceback.format_exc())
            event_type = "tool.job.cancelled" if job.cancel_requested else "tool.job.failed"
            self.event_bus.publish(Event(type=event_type, source=tool.spec.name, call_id=job.call_id, job_id=job.job_id, payload={"error": str(e), "traceback": traceback.format_exc()}))

    def _error(self, tool_name: str, call_id: str, error_type: str, message: str, tb: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        self.last_error = message
        out = {"ok": False, "tool": tool_name, "call_id": call_id, "async": False, "error_type": error_type, "message": message}
        if tb:
            out["traceback"] = tb
        if extra:
            out.update(extra)
        self.event_bus.publish(Event(type="tool.call.failed", source=tool_name, call_id=call_id, payload=out))
        return out

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
