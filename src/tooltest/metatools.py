from __future__ import annotations

from typing import Any

from .tools import ToolRegistry, ToolSpec
from .events import Event


def register_runtime_metatools(registry: ToolRegistry, runtime_getter):
    """Register builtin metatools. runtime_getter returns ToolRuntime lazily."""

    @registry.register(ToolSpec(
        name="runtime_list_jobs",
        kind="meta",
        mode="sync",
        description="List tool runtime jobs, optionally filtered by state.",
        input_schema={"type": "object", "properties": {"state": {"type": "string"}}},
        output_schema={"type": "object", "properties": {"jobs": {"type": "array"}}, "required": ["jobs"]},
    ))
    def runtime_list_jobs(args: dict[str, Any], ctx):
        rt = runtime_getter()
        jobs = [j.to_dict() for j in rt.jobs.list(args.get("state"))]
        return {"jobs": jobs}

    @registry.register(ToolSpec(
        name="runtime_read_job",
        kind="meta",
        mode="sync",
        description="Read one tool job by job_id.",
        input_schema={"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
        output_schema={"type": "object", "properties": {"job": {"type": "object"}}, "required": ["job"]},
    ))
    def runtime_read_job(args: dict[str, Any], ctx):
        rt = runtime_getter()
        job = rt.jobs.get(args["job_id"])
        if not job:
            return {"job": {"ok": False, "error_type": "not_found", "message": f"job not found: {args['job_id']}"}}
        return {"job": job.to_dict()}

    @registry.register(ToolSpec(
        name="runtime_wait_job",
        kind="meta",
        mode="sync",
        description="Wait for a job to complete up to timeout_seconds.",
        input_schema={"type": "object", "properties": {"job_id": {"type": "string"}, "timeout_seconds": {"type": "number"}}, "required": ["job_id", "timeout_seconds"]},
        output_schema={"type": "object", "properties": {"wait_result": {"type": "object"}}, "required": ["wait_result"]},
    ))
    def runtime_wait_job(args: dict[str, Any], ctx):
        rt = runtime_getter()
        return {"wait_result": rt.jobs.wait(args["job_id"], float(args["timeout_seconds"]))}

    @registry.register(ToolSpec(
        name="runtime_switch_job_to_background",
        kind="meta",
        mode="sync",
        description="Switch a foreground switchable job to background view mode.",
        input_schema={"type": "object", "properties": {"job_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["job_id"]},
        output_schema={"type": "object", "properties": {"switch_result": {"type": "object"}}, "required": ["switch_result"]},
    ))
    def runtime_switch_job_to_background(args: dict[str, Any], ctx):
        rt = runtime_getter()
        reason = args.get("reason", "metatool_requested")
        result = rt.jobs.switch_to_background(args["job_id"], reason)
        job = rt.jobs.get(args["job_id"])
        if result.get("ok") and job:
            rt.event_bus.publish(Event(
                type="tool.job.switched_to_background",
                source="runtime",
                job_id=args["job_id"],
                payload={"reason": reason},
            ))
        return {"switch_result": result}

    @registry.register(ToolSpec(
        name="runtime_cancel_job",
        kind="meta",
        mode="sync",
        description="Request cancellation for a running job.",
        input_schema={"type": "object", "properties": {"job_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["job_id"]},
        output_schema={"type": "object", "properties": {"cancel_result": {"type": "object"}}, "required": ["cancel_result"]},
    ))
    def runtime_cancel_job(args: dict[str, Any], ctx):
        rt = runtime_getter()
        result = rt.jobs.cancel(args["job_id"], args.get("reason", "metatool_requested"))
        return {"cancel_result": result}

    @registry.register(ToolSpec(
        name="runtime_read_job_events",
        kind="meta",
        mode="sync",
        description="Read recent events recorded on a job.",
        input_schema={"type": "object", "properties": {"job_id": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["job_id"]},
        output_schema={"type": "object", "properties": {"events": {"type": "array"}}, "required": ["events"]},
    ))
    def runtime_read_job_events(args: dict[str, Any], ctx):
        rt = runtime_getter()
        job = rt.jobs.get(args["job_id"])
        if not job:
            return {"events": []}
        limit = int(args.get("limit", 20))
        return {"events": job.events[-limit:]}

    @registry.register(ToolSpec(
        name="runtime_list_tools",
        kind="meta",
        mode="sync",
        description="List all registered tools including metatools.",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"tools": {"type": "array"}}, "required": ["tools"]},
    ))
    def runtime_list_tools(args: dict[str, Any], ctx):
        rt = runtime_getter()
        return {"tools": rt.registry.specs_as_dicts()}

    @registry.register(ToolSpec(
        name="runtime_exit_agent",
        kind="meta",
        mode="sync",
        description="Exit the current ToolTest agent/app process.",
        input_schema={
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "confirm": {"type": "boolean"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "accepted": {"type": "boolean"},
                "reason": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["accepted", "reason", "message"],
        },
    ))
    def runtime_exit_agent(args: dict[str, Any], ctx):
        reason = str(args.get("reason", "agent_requested_exit"))
        confirm = bool(args.get("confirm", False))
        if not confirm:
            return {
                "accepted": False,
                "reason": reason,
                "message": "set confirm=true to exit agent",
            }
        rt = runtime_getter()
        rt.event_bus.publish(Event(type="runtime.agent.exit_requested", source="runtime", payload={"reason": reason}))
        return {
            "accepted": True,
            "reason": reason,
            "message": "exit requested",
        }
