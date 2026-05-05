from __future__ import annotations

import argparse
import json
from pathlib import Path

from pmo_mvp.demo_data import build_demo_state
from pmo_mvp.engine import PMOCycleEngine
from pmo_mvp.memory import MemoryStore, MemoryToolset
from pmo_mvp.store import JsonStateStore


ROOT = Path(__file__).resolve().parent
RUNTIME_PATH = ROOT / "runtime" / "state.json"
MEMORY_DB_PATH = ROOT / "runtime" / "memory.db"
OUTPUT_DIR = ROOT / "output"
UPLOADS_DIR = ROOT / "uploads"


def _open_memory() -> MemoryStore:
    return MemoryStore(db_path=MEMORY_DB_PATH)


def _toolset() -> MemoryToolset:
    return MemoryToolset(_open_memory())


def init_demo() -> None:
    store = JsonStateStore(RUNTIME_PATH)
    store.save(build_demo_state())
    print(f"demo state initialized at: {RUNTIME_PATH}")


def run_cycle(*, with_memory: bool) -> None:
    store = JsonStateStore(RUNTIME_PATH)
    if not RUNTIME_PATH.exists():
        store.save(build_demo_state())
    memory = _open_memory() if with_memory else None
    engine = PMOCycleEngine(store=store, output_dir=OUTPUT_DIR, memory=memory)
    summary = engine.run()
    if memory is not None:
        memory.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def show_state() -> None:
    if not RUNTIME_PATH.exists():
        raise SystemExit("state file not found, run `python3 run_demo.py init-demo` first")
    data = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
    print(json.dumps(data, ensure_ascii=False, indent=2))


def memory_write(args) -> None:
    tools = _toolset()
    result = tools.dispatch(
        "memory_write",
        {
            "content": args.content,
            "memory_type": args.type,
            "agent_id": args.agent,
            "run_id": args.run_id,
            "project_id": args.project,
            "tags": args.tags or [],
            "importance": args.importance,
            "confidence": args.confidence,
            "expires_at": args.expires_at,
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def memory_search(args) -> None:
    tools = _toolset()
    result = tools.dispatch(
        "memory_search",
        {
            "query": args.query,
            "top_k": args.top_k,
            "memory_type": args.type,
            "agent_id": args.agent,
            "project_id": args.project,
            "run_id": args.run_id,
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def memory_reflect(args) -> None:
    tools = _toolset()
    result = tools.dispatch(
        "memory_reflect",
        {"topic": args.topic, "agent_id": args.agent, "project_id": args.project},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def doc_ingest(args) -> None:
    tools = _toolset()
    target = Path(args.path)
    if not target.is_absolute():
        target = (UPLOADS_DIR / target).resolve()
    result = tools.dispatch(
        "doc_ingest",
        {
            "file_path": str(target),
            "source_type": args.source_type,
            "corpus_id": args.corpus,
            "project_id": args.project,
            "agent_id": args.agent,
            "run_id": args.run_id,
            "uploaded_by": args.uploaded_by,
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def doc_search(args) -> None:
    tools = _toolset()
    result = tools.dispatch(
        "doc_search",
        {
            "query": args.query,
            "top_k": args.top_k,
            "corpus_id": args.corpus,
            "project_id": args.project,
            "agent_id": args.agent,
            "run_id": args.run_id,
            "source_type": args.source_type,
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def memory_list(args) -> None:
    store = _open_memory()
    records = store.list_memories(
        agent_id=args.agent,
        project_id=args.project,
        run_id=args.run_id,
        memory_type=args.type,
        limit=args.limit,
    )
    store.close()
    print(json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2))


def profile_write(args) -> None:
    tools = _toolset()
    result = tools.dispatch(
        "profile_write",
        {
            "profile_type": args.profile_type,
            "owner_id": args.owner_id,
            "content": args.content,
            "overwrite": not args.no_overwrite,
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def profile_read(args) -> None:
    tools = _toolset()
    result = tools.dispatch(
        "profile_read",
        {"profile_type": args.profile_type, "owner_id": args.owner_id},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def session_list(args) -> None:
    store = _open_memory()
    sessions = store.list_sessions(
        project_id=args.project,
        agent_id=args.agent,
        status=args.status,
        limit=args.limit,
    )
    store.close()
    print(json.dumps([item.to_dict() for item in sessions], ensure_ascii=False, indent=2))


def process_list(args) -> None:
    store = _open_memory()
    events = store.list_process_events(
        project_id=args.project,
        agent_id=args.agent,
        run_id=args.run_id,
        event_type=args.event_type,
        query=args.query,
        since=args.since,
        limit=args.limit,
    )
    store.close()
    print(json.dumps([item.to_dict() for item in events], ensure_ascii=False, indent=2))


def project_context(args) -> None:
    store = _open_memory()
    result = store.get_project_context(args.project)
    store.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def memory_evict(args) -> None:
    store = _open_memory()
    deleted = store.evict_memories(
        project_id=args.project,
        agent_id=args.agent,
        run_id=args.run_id,
        memory_type=args.type,
        now=args.now,
        min_importance=args.min_importance,
        max_records=args.max_records,
    )
    store.close()
    print(json.dumps({"count": len(deleted), "deleted_ids": deleted}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PMO multi-agent MVP demo.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-demo", help="Create demo runtime data.")

    p_run = subparsers.add_parser("run-cycle", help="Run one PMO agent cycle.")
    p_run.add_argument(
        "--no-memory",
        action="store_true",
        help="Skip persisting episodic memories to the long-term store.",
    )

    subparsers.add_parser("show-state", help="Print current runtime state.")

    p_write = subparsers.add_parser("memory-write", help="Write a memory record.")
    p_write.add_argument("--content", required=True)
    p_write.add_argument("--agent", required=True)
    p_write.add_argument("--type", choices=["episodic", "reflective", "procedural"], required=True)
    p_write.add_argument("--project")
    p_write.add_argument("--run-id")
    p_write.add_argument("--tags", nargs="*")
    p_write.add_argument("--importance", type=float, default=1.0)
    p_write.add_argument("--confidence", type=float, default=1.0)
    p_write.add_argument("--expires-at")

    p_search = subparsers.add_parser("memory-search", help="Hybrid search over agent memories.")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--top-k", type=int, default=10)
    p_search.add_argument("--type", choices=["episodic", "reflective", "procedural", "all"])
    p_search.add_argument("--agent")
    p_search.add_argument("--project")
    p_search.add_argument("--run-id")

    p_reflect = subparsers.add_parser("memory-reflect", help="Reflect on a topic and store insight.")
    p_reflect.add_argument("--topic", required=True)
    p_reflect.add_argument("--agent", required=True)
    p_reflect.add_argument("--project")

    p_list = subparsers.add_parser("memory-list", help="List recent memories.")
    p_list.add_argument("--agent")
    p_list.add_argument("--project")
    p_list.add_argument("--run-id")
    p_list.add_argument("--type", choices=["episodic", "reflective", "procedural"])
    p_list.add_argument("--limit", type=int, default=20)

    p_profile_write = subparsers.add_parser("profile-write", help="Write AGENT.md or PROJECT.md point memory.")
    p_profile_write.add_argument("--profile-type", choices=["agent", "project"], required=True)
    p_profile_write.add_argument("--owner-id", required=True)
    p_profile_write.add_argument("--content", required=True)
    p_profile_write.add_argument("--no-overwrite", action="store_true")

    p_profile_read = subparsers.add_parser("profile-read", help="Read AGENT.md or PROJECT.md point memory.")
    p_profile_read.add_argument("--profile-type", choices=["agent", "project"], required=True)
    p_profile_read.add_argument("--owner-id", required=True)

    p_sessions = subparsers.add_parser("session-list", help="List AgentSession short-term memories.")
    p_sessions.add_argument("--agent")
    p_sessions.add_argument("--project")
    p_sessions.add_argument("--status")
    p_sessions.add_argument("--limit", type=int, default=20)

    p_process = subparsers.add_parser("process-list", help="List process memory events.")
    p_process.add_argument("--agent")
    p_process.add_argument("--project")
    p_process.add_argument("--run-id")
    p_process.add_argument("--event-type")
    p_process.add_argument("--query")
    p_process.add_argument("--since")
    p_process.add_argument("--limit", type=int, default=50)

    p_context = subparsers.add_parser("project-context", help="Show PROJECT.md, members, artifacts, and events.")
    p_context.add_argument("--project", required=True)

    p_evict = subparsers.add_parser("memory-evict", help="Evict expired or low-weight long-term memories.")
    p_evict.add_argument("--agent")
    p_evict.add_argument("--project")
    p_evict.add_argument("--run-id")
    p_evict.add_argument("--type", choices=["episodic", "reflective", "procedural", "all"])
    p_evict.add_argument("--now")
    p_evict.add_argument("--min-importance", type=float)
    p_evict.add_argument("--max-records", type=int)

    p_ingest = subparsers.add_parser("doc-ingest", help="Ingest a user document.")
    p_ingest.add_argument("--path", required=True, help="Absolute path or filename inside uploads/")
    p_ingest.add_argument(
        "--source-type",
        choices=["knowledge", "instruction", "domain_data"],
        default="knowledge",
    )
    p_ingest.add_argument("--corpus", required=True)
    p_ingest.add_argument("--project")
    p_ingest.add_argument("--agent")
    p_ingest.add_argument("--run-id")
    p_ingest.add_argument("--uploaded-by")

    p_dsearch = subparsers.add_parser("doc-search", help="Search ingested documents.")
    p_dsearch.add_argument("--query", required=True)
    p_dsearch.add_argument("--top-k", type=int, default=5)
    p_dsearch.add_argument("--corpus")
    p_dsearch.add_argument("--project")
    p_dsearch.add_argument("--agent")
    p_dsearch.add_argument("--run-id")
    p_dsearch.add_argument("--source-type", choices=["knowledge", "instruction", "domain_data", "all"])

    args = parser.parse_args()

    if args.command == "init-demo":
        init_demo()
    elif args.command == "run-cycle":
        run_cycle(with_memory=not args.no_memory)
    elif args.command == "show-state":
        show_state()
    elif args.command == "memory-write":
        memory_write(args)
    elif args.command == "memory-search":
        memory_search(args)
    elif args.command == "memory-reflect":
        memory_reflect(args)
    elif args.command == "memory-list":
        memory_list(args)
    elif args.command == "profile-write":
        profile_write(args)
    elif args.command == "profile-read":
        profile_read(args)
    elif args.command == "session-list":
        session_list(args)
    elif args.command == "process-list":
        process_list(args)
    elif args.command == "project-context":
        project_context(args)
    elif args.command == "memory-evict":
        memory_evict(args)
    elif args.command == "doc-ingest":
        doc_ingest(args)
    elif args.command == "doc-search":
        doc_search(args)


if __name__ == "__main__":
    main()
