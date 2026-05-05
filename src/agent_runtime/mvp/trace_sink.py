from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.session import AgentSession


class LocalJsonlTraceSink:
    """Append session snapshots as JSON lines under ``local_trace_dir``."""

    def __init__(self, local_trace_dir: Path) -> None:
        self._dir = Path(local_trace_dir)

    async def on_session_start(self, session: AgentSession) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{session.run_id}.jsonl"
        line = json.dumps(
            {"phase": "start", "run_id": session.run_id, "agent_name": str(session.agent_name)},
            ensure_ascii=False,
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    async def on_session_end(self, session: AgentSession) -> None:
        path = self._dir / f"{session.run_id}.jsonl"
        payload = session.model_dump(mode="json")
        line = json.dumps({"phase": "end", "session": payload}, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    async def on_error(self, session: AgentSession, error: Exception) -> None:
        path = self._dir / f"{session.run_id}.jsonl"
        line = json.dumps(
            {"phase": "error", "run_id": session.run_id, "error": str(error)},
            ensure_ascii=False,
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
