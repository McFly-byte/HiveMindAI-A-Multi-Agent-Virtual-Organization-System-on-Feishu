import json
from pathlib import Path
from typing import Any
from agent_runtime.session import AgentSession
from config.settings import get_settings


class MemoryService:
    """MVP memory service. Feishu Base remains the source of truth."""
    def __init__(self, file_path: Path | None = None) -> None:
        self.file_path = file_path or get_settings().memory_local_file

    def get_project_process_memory(self, project_id: str) -> list[dict[str, Any]]:
        """TODO: read relevant historical session records for a project."""
        _ = project_id
        return []

    def save_session_memory(self, session: AgentSession) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.file_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(session.model_dump(mode="json"), ensure_ascii=False) + "\n")

    def search_history(self, project_id: str, query: str) -> list[dict[str, Any]]:
        """TODO: replace with SQLite / FTS5 if MVP needs richer retrieval."""
        _ = project_id, query
        return []
