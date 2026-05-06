from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_hive.memory.runtime_compat import MemoryStore, MemoryToolset, NullVectorBackend


class MemoryManager:
    """Direct memory access for agents.

    The manager preserves existing memory tool semantics by dispatching through
    ``MemoryToolset`` with the old tool names.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._store = MemoryStore(db_path=self.db_path, vector_backend=NullVectorBackend())
        self._toolset = MemoryToolset(self._store)

    async def search(
        self,
        *,
        query: str,
        agent_id: str,
        project_id: str | None = None,
        run_id: str | None = None,
        top_k: int = 8,
        scopes: list[str] | None = None,
        memory_type: str = "all",
    ) -> list[dict[str, Any]]:
        args: dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "memory_type": memory_type,
            "project_id": project_id,
        }
        scope_set = set(scopes or ["self", "project"])
        if "self" in scope_set:
            args["agent_id"] = agent_id
        if "run" in scope_set and run_id:
            args["run_id"] = run_id
        result = self._toolset.dispatch("memory_search", args)
        if not result.get("ok"):
            return []
        payload = result.get("result") if isinstance(result.get("result"), dict) else {}
        records = payload.get("results") if isinstance(payload, dict) else []
        return records if isinstance(records, list) else []

    async def write(
        self,
        *,
        content: str,
        agent_id: str,
        memory_type: str = "episodic",
        project_id: str | None = None,
        run_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        importance: float = 1.0,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        args = {
            "content": content,
            "memory_type": memory_type,
            "agent_id": agent_id,
            "project_id": project_id,
            "run_id": run_id,
            "tags": tags or [],
            "metadata": metadata or {},
            "importance": importance,
            "confidence": confidence,
        }
        result = self._toolset.dispatch("memory_write", args)
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "memory_write failed"))
        payload = result.get("result")
        return payload if isinstance(payload, dict) else {"result": payload}

    async def reflect(
        self,
        *,
        topic: str,
        agent_id: str,
        project_id: str | None = None,
        lookback: int = 20,
    ) -> dict[str, Any]:
        args = {"topic": topic, "agent_id": agent_id, "project_id": project_id, "lookback": lookback}
        result = self._toolset.dispatch("memory_reflect", args)
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "memory_reflect failed"))
        payload = result.get("result")
        return payload if isinstance(payload, dict) else {"result": payload}

    async def close(self) -> None:
        self._store.close()
