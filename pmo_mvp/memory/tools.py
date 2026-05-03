from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .store import MemoryStore


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "memory_write",
        "description": (
            "Persist an observation, decision, or learned fact to long-term memory. "
            "Call after a run to record outcomes (episodic), after reflection to record "
            "patterns (reflective), or when you discover a stable how-to fact (procedural)."
        ),
        "input_schema": {
            "type": "object",
            "required": ["content", "memory_type", "agent_id"],
            "properties": {
                "content":     {"type": "string"},
                "memory_type": {"type": "string", "enum": ["episodic", "reflective", "procedural"]},
                "agent_id":    {"type": "string"},
                "run_id":      {"type": "string"},
                "project_id":  {"type": "string"},
                "tags":        {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "memory_search",
        "description": (
            "Retrieve memories relevant to a query, ranked by hybrid (vector + BM25) "
            "fusion. Use at the start of a run to load prior context."
        ),
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query":       {"type": "string"},
                "top_k":       {"type": "integer", "default": 10},
                "memory_type": {"type": "string", "enum": ["episodic", "reflective", "procedural", "all"]},
                "agent_id":    {"type": "string"},
                "project_id":  {"type": "string"},
                "since":       {"type": "string", "description": "ISO timestamp; only memories created at or after this point"},
            },
        },
    },
    {
        "name": "memory_get",
        "description": "Retrieve a specific memory by ID.",
        "input_schema": {
            "type": "object",
            "required": ["memory_id"],
            "properties": {"memory_id": {"type": "string"}},
        },
    },
    {
        "name": "memory_reflect",
        "description": (
            "Synthesize recent episodic memories on a topic into a single reflective "
            "insight, then store the insight as a new reflective memory. Returns the "
            "stored reflection."
        ),
        "input_schema": {
            "type": "object",
            "required": ["topic", "agent_id"],
            "properties": {
                "topic":       {"type": "string"},
                "agent_id":    {"type": "string"},
                "project_id":  {"type": "string"},
                "lookback":    {"type": "integer", "default": 20, "description": "Max episodic memories to consider"},
            },
        },
    },
    {
        "name": "doc_ingest",
        "description": (
            "Ingest a user-uploaded document into the knowledge base. Reads the file, "
            "chunks it, and indexes each chunk for retrieval."
        ),
        "input_schema": {
            "type": "object",
            "required": ["file_path", "source_type", "corpus_id"],
            "properties": {
                "file_path":   {"type": "string"},
                "source_type": {"type": "string", "enum": ["knowledge", "instruction", "domain_data"]},
                "corpus_id":   {"type": "string"},
                "uploaded_by": {"type": "string"},
                "metadata":    {"type": "object"},
            },
        },
    },
    {
        "name": "doc_search",
        "description": "Search user-uploaded document chunks for content relevant to the query.",
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query":       {"type": "string"},
                "top_k":       {"type": "integer", "default": 5},
                "corpus_id":   {"type": "string"},
                "source_type": {"type": "string", "enum": ["knowledge", "instruction", "domain_data", "all"]},
            },
        },
    },
]


# Reflection synthesizer is pluggable so callers can wire up an LLM later.
# Default is a deterministic concatenation summary so the system runs without one.
ReflectFn = Callable[[str, list[dict]], str]


def default_reflect(topic: str, episodes: list[dict]) -> str:
    if not episodes:
        return f"关于「{topic}」当前没有可参考的历史观察。"
    bullets = []
    for item in episodes[:10]:
        ts = (item.get("created_at") or "")[:10]
        bullets.append(f"- ({ts}) {item['content']}")
    return (
        f"关于「{topic}」从近期 {len(episodes)} 条观察中提炼：\n"
        + "\n".join(bullets)
        + "\n\n模式提示：以上事件反复出现说明可能存在系统性问题，值得在下一周期主动核查。"
    )


class MemoryToolset:
    """Dispatches tool calls to the underlying MemoryStore.

    LLM-driven agents call `dispatch(name, arguments)`. Pure-Python agents
    call the typed methods directly via `store`.
    """

    def __init__(self, store: MemoryStore, reflect_fn: ReflectFn | None = None) -> None:
        self.store = store
        self.reflect_fn = reflect_fn or default_reflect
        self._handlers: dict[str, Callable[[dict], Any]] = {
            "memory_write": self._memory_write,
            "memory_search": self._memory_search,
            "memory_get": self._memory_get,
            "memory_reflect": self._memory_reflect,
            "doc_ingest": self._doc_ingest,
            "doc_search": self._doc_search,
        }

    # ---- public ------------------------------------------------------

    def schemas(self) -> list[dict]:
        return TOOL_SCHEMAS

    def dispatch(self, name: str, arguments: dict) -> dict:
        handler = self._handlers.get(name)
        if handler is None:
            return {"error": f"unknown tool: {name}"}
        try:
            return {"ok": True, "result": handler(arguments or {})}
        except Exception as exc:  # surface errors as tool results
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ---- handlers ----------------------------------------------------

    def _memory_write(self, args: dict) -> dict:
        record = self.store.write_memory(
            content=args["content"],
            agent_id=args["agent_id"],
            memory_type=args["memory_type"],
            run_id=args.get("run_id"),
            project_id=args.get("project_id"),
            tags=args.get("tags"),
        )
        return record.to_dict()

    def _memory_search(self, args: dict) -> dict:
        results = self.store.search_memories(
            query=args["query"],
            top_k=args.get("top_k", 10),
            memory_type=args.get("memory_type"),
            agent_id=args.get("agent_id"),
            project_id=args.get("project_id"),
            since=args.get("since"),
        )
        return {"count": len(results), "results": [r.to_dict() for r in results]}

    def _memory_get(self, args: dict) -> dict:
        record = self.store.get_memory(args["memory_id"])
        return record.to_dict() if record else {"error": "not_found"}

    def _memory_reflect(self, args: dict) -> dict:
        topic = args["topic"]
        agent_id = args["agent_id"]
        project_id = args.get("project_id")
        lookback = args.get("lookback", 20)

        episodes = self.store.search_memories(
            query=topic,
            top_k=lookback,
            memory_type="episodic",
            agent_id=agent_id,
            project_id=project_id,
        )
        episode_dicts = [e.to_dict() for e in episodes]
        synthesis = self.reflect_fn(topic, episode_dicts)
        record = self.store.write_memory(
            content=synthesis,
            agent_id=agent_id,
            memory_type="reflective",
            project_id=project_id,
            tags=["reflection", topic],
        )
        return {"reflection": record.to_dict(), "source_count": len(episode_dicts)}

    def _doc_ingest(self, args: dict) -> dict:
        return self.store.ingest_document(
            file_path=Path(args["file_path"]),
            source_type=args["source_type"],
            corpus_id=args["corpus_id"],
            uploaded_by=args.get("uploaded_by"),
            metadata=args.get("metadata"),
        )

    def _doc_search(self, args: dict) -> dict:
        results = self.store.search_documents(
            query=args["query"],
            top_k=args.get("top_k", 5),
            corpus_id=args.get("corpus_id"),
            source_type=args.get("source_type"),
        )
        return {"count": len(results), "results": [r.to_dict() for r in results]}
