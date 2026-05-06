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
                "importance":  {"type": "number", "default": 1.0},
                "confidence":  {"type": "number", "default": 1.0},
                "expires_at":  {"type": "string"},
                "metadata":    {"type": "object"},
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
                "run_id":      {"type": "string"},
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
        "name": "profile_write",
        "description": "Write point memory to an agent AGENT.md or project PROJECT.md file.",
        "input_schema": {
            "type": "object",
            "required": ["profile_type", "owner_id", "content"],
            "properties": {
                "profile_type": {"type": "string", "enum": ["agent", "project"]},
                "owner_id":     {"type": "string"},
                "content":      {"type": "string"},
                "overwrite":    {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "profile_read",
        "description": "Read point memory from an agent AGENT.md or project PROJECT.md file.",
        "input_schema": {
            "type": "object",
            "required": ["profile_type", "owner_id"],
            "properties": {
                "profile_type": {"type": "string", "enum": ["agent", "project"]},
                "owner_id":     {"type": "string"},
            },
        },
    },
    {
        "name": "session_start",
        "description": "Start a short-term AgentSession for one agent run.",
        "input_schema": {
            "type": "object",
            "required": ["agent_id"],
            "properties": {
                "agent_id":      {"type": "string"},
                "run_id":        {"type": "string"},
                "project_id":    {"type": "string"},
                "input_summary": {"type": "string"},
                "scratchpad":    {"type": "string"},
                "metadata":      {"type": "object"},
            },
        },
    },
    {
        "name": "session_finish",
        "description": "Finish an AgentSession and persist its output summary.",
        "input_schema": {
            "type": "object",
            "required": ["run_id"],
            "properties": {
                "run_id":         {"type": "string"},
                "status":         {"type": "string", "enum": ["completed", "failed", "cancelled", "running"], "default": "completed"},
                "output_summary": {"type": "string"},
                "scratchpad":     {"type": "string"},
                "metadata":       {"type": "object"},
            },
        },
    },
    {
        "name": "session_get",
        "description": "Get a short-term AgentSession by run_id.",
        "input_schema": {
            "type": "object",
            "required": ["run_id"],
            "properties": {"run_id": {"type": "string"}},
        },
    },
    {
        "name": "session_list",
        "description": "List recent AgentSessions by project_id, agent_id, or status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "agent_id":   {"type": "string"},
                "status":     {"type": "string"},
                "limit":      {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "process_log",
        "description": "Append a project-bound process event for audit and run traceability.",
        "input_schema": {
            "type": "object",
            "required": ["event_type", "message"],
            "properties": {
                "event_type": {"type": "string"},
                "message":    {"type": "string"},
                "project_id": {"type": "string"},
                "agent_id":   {"type": "string"},
                "run_id":     {"type": "string"},
                "payload":    {"type": "object"},
            },
        },
    },
    {
        "name": "process_search",
        "description": "Search or list process events by project_id, agent_id, run_id, event_type, query, and since.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "agent_id":   {"type": "string"},
                "run_id":     {"type": "string"},
                "event_type": {"type": "string"},
                "query":      {"type": "string"},
                "since":      {"type": "string"},
                "limit":      {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "project_context_upsert",
        "description": "Upsert project profile content, members, and artifacts for process memory.",
        "input_schema": {
            "type": "object",
            "required": ["project_id"],
            "properties": {
                "project_id":      {"type": "string"},
                "profile_content": {"type": "string"},
                "members":         {"type": "array", "items": {"type": "object"}},
                "artifacts":       {"type": "array", "items": {"type": "object"}},
            },
        },
    },
    {
        "name": "project_context_get",
        "description": "Get a project's PROJECT.md, members, artifacts, and recent process events.",
        "input_schema": {
            "type": "object",
            "required": ["project_id"],
            "properties": {"project_id": {"type": "string"}},
        },
    },
    {
        "name": "memory_weight_update",
        "description": "Update importance, confidence, expiry, or metadata for a long-term memory.",
        "input_schema": {
            "type": "object",
            "required": ["memory_id"],
            "properties": {
                "memory_id":  {"type": "string"},
                "importance": {"type": "number"},
                "confidence": {"type": "number"},
                "expires_at": {"type": "string"},
                "metadata":   {"type": "object"},
            },
        },
    },
    {
        "name": "memory_evict",
        "description": "Evict expired, low-importance, or over-budget long-term memories in a scope.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id":      {"type": "string"},
                "agent_id":        {"type": "string"},
                "run_id":          {"type": "string"},
                "memory_type":     {"type": "string", "enum": ["episodic", "reflective", "procedural", "all"]},
                "now":             {"type": "string"},
                "min_importance":  {"type": "number"},
                "max_records":     {"type": "integer"},
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
                "project_id":  {"type": "string"},
                "agent_id":    {"type": "string"},
                "run_id":      {"type": "string"},
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
                "project_id":  {"type": "string"},
                "agent_id":    {"type": "string"},
                "run_id":      {"type": "string"},
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
            "profile_write": self._profile_write,
            "profile_read": self._profile_read,
            "session_start": self._session_start,
            "session_finish": self._session_finish,
            "session_get": self._session_get,
            "session_list": self._session_list,
            "process_log": self._process_log,
            "process_search": self._process_search,
            "project_context_upsert": self._project_context_upsert,
            "project_context_get": self._project_context_get,
            "memory_weight_update": self._memory_weight_update,
            "memory_evict": self._memory_evict,
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
            importance=args.get("importance", 1.0),
            confidence=args.get("confidence", 1.0),
            expires_at=args.get("expires_at"),
            metadata=args.get("metadata"),
        )
        return record.to_dict()

    def _memory_search(self, args: dict) -> dict:
        results = self.store.search_memories(
            query=args["query"],
            top_k=args.get("top_k", 10),
            memory_type=args.get("memory_type"),
            agent_id=args.get("agent_id"),
            project_id=args.get("project_id"),
            run_id=args.get("run_id"),
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
            importance=args.get("importance", 1.0),
            metadata={"source": "memory_reflect", "source_count": len(episode_dicts)},
        )
        return {"reflection": record.to_dict(), "source_count": len(episode_dicts)}

    def _profile_write(self, args: dict) -> dict:
        if args["profile_type"] == "agent":
            record = self.store.write_agent_prompt(
                agent_id=args["owner_id"],
                content=args["content"],
                overwrite=args.get("overwrite", True),
            )
        else:
            record = self.store.write_project_profile(
                project_id=args["owner_id"],
                content=args["content"],
                overwrite=args.get("overwrite", True),
            )
        return record.to_dict()

    def _profile_read(self, args: dict) -> dict:
        if args["profile_type"] == "agent":
            record = self.store.read_agent_prompt(args["owner_id"])
        else:
            record = self.store.read_project_profile(args["owner_id"])
        return record.to_dict() if record else {"error": "not_found"}

    def _session_start(self, args: dict) -> dict:
        session = self.store.start_session(
            agent_id=args["agent_id"],
            run_id=args.get("run_id"),
            project_id=args.get("project_id"),
            input_summary=args.get("input_summary"),
            scratchpad=args.get("scratchpad"),
            metadata=args.get("metadata"),
        )
        return session.to_dict()

    def _session_finish(self, args: dict) -> dict:
        session = self.store.finish_session(
            run_id=args["run_id"],
            status=args.get("status", "completed"),
            output_summary=args.get("output_summary"),
            scratchpad=args.get("scratchpad"),
            metadata=args.get("metadata"),
        )
        return session.to_dict()

    def _session_get(self, args: dict) -> dict:
        session = self.store.get_session(args["run_id"])
        return session.to_dict() if session else {"error": "not_found"}

    def _session_list(self, args: dict) -> dict:
        sessions = self.store.list_sessions(
            project_id=args.get("project_id"),
            agent_id=args.get("agent_id"),
            status=args.get("status"),
            limit=args.get("limit", 50),
        )
        return {"count": len(sessions), "results": [item.to_dict() for item in sessions]}

    def _process_log(self, args: dict) -> dict:
        event = self.store.record_process_event(
            event_type=args["event_type"],
            message=args["message"],
            project_id=args.get("project_id"),
            agent_id=args.get("agent_id"),
            run_id=args.get("run_id"),
            payload=args.get("payload"),
        )
        return event.to_dict()

    def _process_search(self, args: dict) -> dict:
        events = self.store.list_process_events(
            project_id=args.get("project_id"),
            agent_id=args.get("agent_id"),
            run_id=args.get("run_id"),
            event_type=args.get("event_type"),
            query=args.get("query"),
            since=args.get("since"),
            limit=args.get("limit", 100),
        )
        return {"count": len(events), "results": [item.to_dict() for item in events]}

    def _project_context_upsert(self, args: dict) -> dict:
        project_id = args["project_id"]
        result: dict[str, Any] = {"project_id": project_id, "members": [], "artifacts": []}
        if args.get("profile_content"):
            result["profile"] = self.store.write_project_profile(
                project_id=project_id, content=args["profile_content"]
            ).to_dict()
        for item in args.get("members") or []:
            result["members"].append(
                self.store.upsert_project_member(project_id=project_id, **item).to_dict()
            )
        for item in args.get("artifacts") or []:
            result["artifacts"].append(
                self.store.upsert_project_artifact(project_id=project_id, **item).to_dict()
            )
        return result

    def _project_context_get(self, args: dict) -> dict:
        return self.store.get_project_context(args["project_id"])

    def _memory_weight_update(self, args: dict) -> dict:
        record = self.store.update_memory_weight(
            args["memory_id"],
            importance=args.get("importance"),
            confidence=args.get("confidence"),
            expires_at=args.get("expires_at"),
            metadata=args.get("metadata"),
        )
        return record.to_dict() if record else {"error": "not_found"}

    def _memory_evict(self, args: dict) -> dict:
        ids = self.store.evict_memories(
            project_id=args.get("project_id"),
            agent_id=args.get("agent_id"),
            run_id=args.get("run_id"),
            memory_type=args.get("memory_type"),
            now=args.get("now"),
            min_importance=args.get("min_importance"),
            max_records=args.get("max_records"),
        )
        return {"count": len(ids), "deleted_ids": ids}

    def _doc_ingest(self, args: dict) -> dict:
        return self.store.ingest_document(
            file_path=Path(args["file_path"]),
            source_type=args["source_type"],
            corpus_id=args["corpus_id"],
            project_id=args.get("project_id"),
            agent_id=args.get("agent_id"),
            run_id=args.get("run_id"),
            uploaded_by=args.get("uploaded_by"),
            metadata=args.get("metadata"),
        )

    def _doc_search(self, args: dict) -> dict:
        results = self.store.search_documents(
            query=args["query"],
            top_k=args.get("top_k", 5),
            corpus_id=args.get("corpus_id"),
            project_id=args.get("project_id"),
            agent_id=args.get("agent_id"),
            run_id=args.get("run_id"),
            source_type=args.get("source_type"),
        )
        return {"count": len(results), "results": [r.to_dict() for r in results]}
