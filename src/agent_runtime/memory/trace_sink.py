from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from agent_runtime.enums import AgentRunStatus
from agent_runtime.session import AgentSession

from .store import MemoryStore


def _checkpoint_status(status: AgentRunStatus | str) -> str:
    raw = str(status.value if isinstance(status, AgentRunStatus) else status)
    if raw in {"success", "partial_success", "completed"}:
        return "completed"
    if raw in {"failed", "timeout"}:
        return "failed"
    if raw == "cancelled":
        return "cancelled"
    return "running"


def _session_metadata(session: AgentSession) -> dict:
    return session.model_dump(mode="json")


class MemoryTraceSink:
    """Persist runtime AgentSession checkpoints to MemoryStore.

    The live loop state remains ``agent_runtime.session.AgentSession``. This
    sink only mirrors start/end/error checkpoints for recovery, audit, and
    cross-run lookup.
    """

    def __init__(self, db_path: Path, file_root: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.file_root = file_root

    def _store(self) -> MemoryStore:
        return MemoryStore(db_path=self.db_path, file_root=self.file_root)

    async def on_session_start(self, session: AgentSession) -> None:
        store = self._store()
        try:
            store.start_session(
                agent_id=str(session.agent_name),
                run_id=session.run_id,
                project_id=session.project_id,
                input_summary=json.dumps(
                    {
                        "trigger_type": str(session.trigger_type),
                        "input_record_ids": session.input_record_ids,
                    },
                    ensure_ascii=False,
                ),
                metadata=_session_metadata(session),
            )
        finally:
            store.close()

    async def on_session_end(self, session: AgentSession) -> None:
        store = self._store()
        try:
            if store.get_session(session.run_id) is None:
                store.start_session(
                    agent_id=str(session.agent_name),
                    run_id=session.run_id,
                    project_id=session.project_id,
                    metadata=_session_metadata(session),
                )
            store.finish_session(
                run_id=session.run_id,
                status=_checkpoint_status(session.status),
                output_summary=session.final_summary,
                scratchpad=json.dumps(
                    {
                        "memory": [item.model_dump(mode="json") for item in session.memory],
                        "steps": [step.model_dump(mode="json") for step in session.steps],
                    },
                    ensure_ascii=False,
                ),
                metadata=_session_metadata(session),
            )
        finally:
            store.close()

    async def on_error(self, session: AgentSession, error: Exception) -> None:
        store = self._store()
        try:
            store.record_process_event(
                project_id=session.project_id,
                agent_id=str(session.agent_name),
                run_id=session.run_id,
                event_type="runtime_error",
                message=str(error),
                payload={"error_class": error.__class__.__name__},
            )
        finally:
            store.close()


class CompositeTraceSink:
    """Fan out trace sink calls to multiple sinks."""

    def __init__(self, sinks: Iterable[object]) -> None:
        self._sinks = list(sinks)

    async def on_session_start(self, session: AgentSession) -> None:
        for sink in self._sinks:
            await sink.on_session_start(session)

    async def on_session_end(self, session: AgentSession) -> None:
        for sink in self._sinks:
            await sink.on_session_end(session)

    async def on_error(self, session: AgentSession, error: Exception) -> None:
        for sink in self._sinks:
            await sink.on_error(session, error)
