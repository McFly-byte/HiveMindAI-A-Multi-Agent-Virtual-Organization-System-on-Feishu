from __future__ import annotations

from typing import Protocol


class VectorBackend(Protocol):
    """Pluggable vector store. Implementations bridge to mem0/Qdrant/etc."""

    def upsert(self, collection: str, vector_id: str, content: str, payload: dict) -> None: ...

    def query(self, collection: str, query: str, top_k: int, filters: dict | None) -> list[dict]: ...

    def delete(self, collection: str, vector_id: str) -> None: ...


class NullVectorBackend:
    """No-op backend. Retrieval falls back to BM25/FTS only."""

    available = False

    def upsert(self, collection: str, vector_id: str, content: str, payload: dict) -> None:
        return None

    def query(self, collection: str, query: str, top_k: int, filters: dict | None) -> list[dict]:
        return []

    def delete(self, collection: str, vector_id: str) -> None:
        return None


class Mem0VectorBackend:
    """Adapter over mem0's Memory client. Used only when mem0 is installed."""

    available = True

    def __init__(self, config: dict | None = None) -> None:
        from mem0 import Memory  # noqa: F401  imported lazily

        self._memory_cls = Memory
        self._clients: dict[str, object] = {}
        self._config = config or {}

    def _client(self, collection: str):
        if collection not in self._clients:
            cfg = dict(self._config)
            cfg.setdefault("vector_store", {}).setdefault(
                "config", {"collection_name": collection}
            )
            self._clients[collection] = self._memory_cls.from_config(cfg) if cfg else self._memory_cls()
        return self._clients[collection]

    def upsert(self, collection: str, vector_id: str, content: str, payload: dict) -> None:
        client = self._client(collection)
        client.add(
            messages=[{"role": "assistant", "content": content}],
            agent_id=payload.get("agent_id"),
            run_id=payload.get("run_id"),
            user_id=payload.get("user_id"),
            metadata={**payload, "external_id": vector_id},
            infer=False,
        )

    def query(self, collection: str, query: str, top_k: int, filters: dict | None) -> list[dict]:
        client = self._client(collection)
        results = client.search(query=query, top_k=top_k, filters=filters or {})
        normalized: list[dict] = []
        for item in results.get("results", []) if isinstance(results, dict) else results:
            metadata = item.get("metadata") or {}
            normalized.append(
                {
                    "id": metadata.get("external_id") or item.get("id"),
                    "score": float(item.get("score", 0.0)),
                    "content": item.get("memory") or item.get("content", ""),
                    "metadata": metadata,
                }
            )
        return normalized

    def delete(self, collection: str, vector_id: str) -> None:
        client = self._client(collection)
        if hasattr(client, "delete"):
            client.delete(memory_id=vector_id)


def build_backend(prefer_mem0: bool = True, config: dict | None = None) -> VectorBackend:
    """Return the best available backend. Falls back to NullVectorBackend silently."""
    if prefer_mem0:
        try:
            return Mem0VectorBackend(config=config)
        except Exception:
            pass
    return NullVectorBackend()
