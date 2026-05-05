from __future__ import annotations

import time
import traceback
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Literal

JobState = Literal["created", "running", "succeeded", "failed", "cancelled", "timeout"]
JobViewMode = Literal["foreground", "background"]


@dataclass
class JobRecord:
    job_id: str
    tool_name: str
    call_id: str
    args: dict[str, Any]
    state: JobState = "created"
    view_mode: JobViewMode = "background"
    progress: float | None = None
    latest_output: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    traceback: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    cancel_requested: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    future: Future | None = field(default=None, repr=False)

    def to_dict(self, include_future: bool = False) -> dict[str, Any]:
        data = {
            "job_id": self.job_id,
            "tool_name": self.tool_name,
            "call_id": self.call_id,
            "args": self.args,
            "state": self.state,
            "view_mode": self.view_mode,
            "progress": self.progress,
            "latest_output": self.latest_output,
            "result": self.result,
            "error": self.error,
            "traceback": self.traceback,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancel_requested": self.cancel_requested,
            "events": list(self.events),
        }
        if include_future:
            data["future"] = repr(self.future)
        return data


class JobRuntime:
    def __init__(self):
        self.jobs: dict[str, JobRecord] = {}

    def create(self, tool_name: str, call_id: str, args: dict[str, Any], view_mode: JobViewMode) -> JobRecord:
        job = JobRecord(
            job_id=f"job_{uuid.uuid4().hex[:12]}",
            tool_name=tool_name,
            call_id=call_id,
            args=args,
            view_mode=view_mode,
        )
        self.jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> JobRecord | None:
        return self.jobs.get(job_id)

    def list(self, state: str | None = None) -> list[JobRecord]:
        jobs = list(self.jobs.values())
        if state:
            jobs = [j for j in jobs if j.state == state]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def set_running(self, job_id: str):
        job = self.jobs[job_id]
        job.state = "running"
        job.started_at = time.time()
        job.updated_at = time.time()

    def set_succeeded(self, job_id: str, result: dict[str, Any]):
        job = self.jobs[job_id]
        job.state = "succeeded"
        job.result = result
        job.finished_at = time.time()
        job.updated_at = job.finished_at
        job.progress = 1.0

    def set_failed(self, job_id: str, error: str, tb: str | None = None):
        job = self.jobs[job_id]
        if job.cancel_requested:
            job.state = "cancelled"
        else:
            job.state = "failed"
        job.error = error
        job.traceback = tb or traceback.format_exc()
        job.finished_at = time.time()
        job.updated_at = job.finished_at

    def update_checkpoint(self, job_id: str, payload: dict[str, Any]):
        job = self.jobs.get(job_id)
        if not job:
            return
        job.latest_output = payload
        if "progress" in payload:
            try:
                job.progress = float(payload["progress"])
            except Exception:
                pass
        job.updated_at = time.time()
        job.events.append({
            "type": "tool.job.checkpoint",
            "created_at": time.time(),
            "payload": payload,
        })

    def switch_to_background(self, job_id: str, reason: str = "requested") -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            return {"ok": False, "error_type": "not_found", "message": f"job not found: {job_id}"}
        previous = job.view_mode
        job.view_mode = "background"
        job.updated_at = time.time()
        job.events.append({"type": "tool.job.switched_to_background", "created_at": time.time(), "payload": {"reason": reason}})
        return {"ok": True, "job_id": job_id, "previous_view_mode": previous, "view_mode": job.view_mode, "reason": reason}

    def cancel(self, job_id: str, reason: str = "requested") -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            return {"ok": False, "error_type": "not_found", "message": f"job not found: {job_id}"}
        job.cancel_requested = True
        if job.state in ("created", "running"):
            cancelled = False
            if job.future:
                cancelled = job.future.cancel()
            if cancelled:
                job.state = "cancelled"
                job.finished_at = time.time()
        job.updated_at = time.time()
        job.events.append({"type": "tool.job.cancel_requested", "created_at": time.time(), "payload": {"reason": reason}})
        return {"ok": True, "job_id": job_id, "state": job.state, "cancel_requested": True, "reason": reason}

    def wait(self, job_id: str, timeout_seconds: float) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            return {"ok": False, "error_type": "not_found", "message": f"job not found: {job_id}"}
        deadline = time.time() + max(0.0, timeout_seconds)
        while time.time() < deadline:
            if job.state not in ("created", "running"):
                return {"ok": True, "timed_out": False, "job": job.to_dict()}
            time.sleep(0.05)
        return {"ok": True, "timed_out": True, "job": job.to_dict()}
