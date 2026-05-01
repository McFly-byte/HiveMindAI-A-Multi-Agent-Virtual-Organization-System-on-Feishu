import json
from pathlib import Path
from agent_runtime.session import AgentSession
from config.settings import get_settings
from schemas.base import utc_now


class TraceService:
    """Write local trace fallback and reserve LangSmith integration."""
    def __init__(self, trace_dir: Path | None = None) -> None:
        self.settings = get_settings()
        self.trace_dir = trace_dir or self.settings.trace_local_dir

    def write_step(self, session: AgentSession, step: str, status: str, summary: str) -> None:
        if self.settings.langsmith_tracing:
            pass  # TODO: send to LangSmith. Local fallback must always remain available.
        day_dir = self.trace_dir / utc_now().date().isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        payload = {"ts": utc_now().isoformat(), "run_id": session.run_id, "agent_name": session.agent_name, "step": step, "status": status, "summary": summary}
        with (day_dir / f"{session.run_id}.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")
