from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .doc_pipeline import chunk_text, read_text_file
from .embedding import VectorBackend, build_backend
from .retrieval import bm25_search, reciprocal_rank_fusion


SCHEMA_PATH = Path(__file__).with_name("schema.sql")

AGENT_COLLECTION = "agent_memories"
DOC_COLLECTION = "user_documents"

VALID_MEMORY_TYPES = {"episodic", "reflective", "procedural"}
VALID_SOURCE_TYPES = {"knowledge", "instruction", "domain_data"}
VALID_SESSION_STATUSES = {"running", "completed", "failed", "cancelled"}

_SAFE_PATH_SEGMENT = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass
class MemoryRecord:
    id: str
    agent_id: str
    run_id: str | None
    project_id: str | None
    memory_type: str
    content: str
    tags: list[str] = field(default_factory=list)
    importance: float = 1.0
    confidence: float = 1.0
    access_count: int = 0
    last_accessed: str | None = None
    expires_at: str | None = None
    metadata: dict = field(default_factory=dict)
    created_at: str | None = None
    score: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "memory_type": self.memory_type,
            "content": self.content,
            "tags": self.tags,
            "importance": self.importance,
            "confidence": self.confidence,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed,
            "expires_at": self.expires_at,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "score": self.score,
        }


@dataclass
class DocumentChunk:
    id: str
    document_id: str
    corpus_id: str
    source_type: str
    chunk_index: int
    content: str
    project_id: str | None = None
    agent_id: str | None = None
    run_id: str | None = None
    score: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "corpus_id": self.corpus_id,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "source_type": self.source_type,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "score": self.score,
        }


@dataclass
class PointMemoryFile:
    kind: str
    owner_id: str
    path: str
    content: str

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "owner_id": self.owner_id,
            "path": self.path,
            "content": self.content,
        }


@dataclass
class AgentSessionCheckpoint:
    """Persisted checkpoint for a runtime AgentSession.

    The live loop session is ``agent_runtime.session.AgentSession``. This
    record is only a durable summary/checkpoint keyed by ``run_id``.
    """

    run_id: str
    agent_id: str
    project_id: str | None = None
    status: str = "running"
    input_summary: str | None = None
    output_summary: str | None = None
    scratchpad: str | None = None
    metadata: dict = field(default_factory=dict)
    started_at: str | None = None
    ended_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "project_id": self.project_id,
            "status": self.status,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "scratchpad": self.scratchpad,
            "metadata": self.metadata,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


@dataclass
class ProcessEvent:
    id: str
    event_type: str
    message: str
    project_id: str | None = None
    agent_id: str | None = None
    run_id: str | None = None
    payload: dict = field(default_factory=dict)
    created_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "event_type": self.event_type,
            "message": self.message,
            "payload": self.payload,
            "created_at": self.created_at,
        }


@dataclass
class ProjectMember:
    id: str
    project_id: str
    member_id: str
    name: str
    role: str
    responsibility: str | None = None
    metadata: dict = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "member_id": self.member_id,
            "name": self.name,
            "role": self.role,
            "responsibility": self.responsibility,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ProjectArtifact:
    id: str
    project_id: str
    artifact_type: str
    name: str
    external_id: str | None = None
    url: str | None = None
    metadata: dict = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "artifact_type": self.artifact_type,
            "name": self.name,
            "external_id": self.external_id,
            "url": self.url,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _hash(content: str, *salts: str) -> str:
    h = hashlib.sha256()
    h.update(content.encode("utf-8"))
    for salt in salts:
        h.update(b"\x1f")
        h.update((salt or "").encode("utf-8"))
    return h.hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _json_dumps(value: dict | list | None) -> str:
    return json.dumps({} if value is None else value, ensure_ascii=False)


def _json_loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _safe_segment(value: str) -> str:
    segment = _SAFE_PATH_SEGMENT.sub("-", (value or "").strip())
    return segment.strip("-") or "default"


class MemoryStore:
    """Long-term memory backed by SQLite (metadata + FTS) and an optional vector backend.

    Two logical collections:
    - Agent-managed memory: facts the agent writes about itself / its runs.
    - User-managed documents: human-uploaded files chunked into searchable spans.

    Both are queryable via the same hybrid retrieval path (vector ⊕ BM25 → RRF).
    """

    def __init__(
        self,
        db_path: Path,
        vector_backend: VectorBackend | None = None,
        file_root: Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_root = Path(file_root) if file_root is not None else self.db_path.with_suffix("")
        self.file_root.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()
        self.vector = vector_backend if vector_backend is not None else build_backend()

    # ---- lifecycle ---------------------------------------------------

    def _init_schema(self) -> None:
        ddl = SCHEMA_PATH.read_text(encoding="utf-8")
        self._conn.executescript(ddl)
        self._migrate_schema()
        self._conn.commit()

    def _migrate_schema(self) -> None:
        """Backfill columns/indexes when opening an older runtime/memory.db."""
        columns = self._table_columns("memories")
        additions = {
            "importance": "ALTER TABLE memories ADD COLUMN importance REAL NOT NULL DEFAULT 1.0",
            "confidence": "ALTER TABLE memories ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0",
            "access_count": "ALTER TABLE memories ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0",
            "last_accessed": "ALTER TABLE memories ADD COLUMN last_accessed TEXT",
            "expires_at": "ALTER TABLE memories ADD COLUMN expires_at TEXT",
            "metadata": "ALTER TABLE memories ADD COLUMN metadata TEXT",
        }
        for column, statement in additions.items():
            if column not in columns:
                self._conn.execute(statement)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_weight ON memories(importance, confidence)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_expires ON memories(expires_at)"
        )
        document_columns = self._table_columns("documents")
        for column in ("project_id", "agent_id", "run_id"):
            if column not in document_columns:
                self._conn.execute(f"ALTER TABLE documents ADD COLUMN {column} TEXT")
        chunk_columns = self._table_columns("doc_chunks")
        for column in ("project_id", "agent_id", "run_id"):
            if column not in chunk_columns:
                self._conn.execute(f"ALTER TABLE doc_chunks ADD COLUMN {column} TEXT")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_project ON doc_chunks(project_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_agent ON doc_chunks(agent_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_run ON doc_chunks(run_id)"
        )

    def _table_columns(self, table: str) -> set[str]:
        return {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}

    def close(self) -> None:
        self._conn.close()

    # ---- point memory (AGENT.md / PROJECT.md) -----------------------

    def write_agent_prompt(
        self, *, agent_id: str, content: str, overwrite: bool = True
    ) -> PointMemoryFile:
        return self._write_point_memory(
            kind="agent",
            owner_id=agent_id,
            filename="AGENT.md",
            content=content,
            overwrite=overwrite,
        )

    def read_agent_prompt(self, agent_id: str) -> PointMemoryFile | None:
        return self._read_point_memory("agent", agent_id, "AGENT.md")

    def ensure_agent_prompt(self, *, agent_id: str, content: str) -> PointMemoryFile:
        return self.write_agent_prompt(agent_id=agent_id, content=content, overwrite=False)

    def write_project_profile(
        self, *, project_id: str, content: str, overwrite: bool = True
    ) -> PointMemoryFile:
        return self._write_point_memory(
            kind="project",
            owner_id=project_id,
            filename="PROJECT.md",
            content=content,
            overwrite=overwrite,
        )

    def read_project_profile(self, project_id: str) -> PointMemoryFile | None:
        return self._read_point_memory("project", project_id, "PROJECT.md")

    def ensure_project_profile(self, *, project_id: str, content: str) -> PointMemoryFile:
        return self.write_project_profile(project_id=project_id, content=content, overwrite=False)

    def _write_point_memory(
        self,
        *,
        kind: str,
        owner_id: str,
        filename: str,
        content: str,
        overwrite: bool,
    ) -> PointMemoryFile:
        content = (content or "").strip()
        if not content:
            raise ValueError("point memory content cannot be empty")

        path = self._point_memory_path(kind, owner_id, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        if overwrite or not path.exists():
            path.write_text(content + "\n", encoding="utf-8")
        return PointMemoryFile(
            kind=kind,
            owner_id=owner_id,
            path=str(path),
            content=path.read_text(encoding="utf-8"),
        )

    def _read_point_memory(
        self, kind: str, owner_id: str, filename: str
    ) -> PointMemoryFile | None:
        path = self._point_memory_path(kind, owner_id, filename)
        if not path.exists():
            return None
        return PointMemoryFile(
            kind=kind,
            owner_id=owner_id,
            path=str(path),
            content=path.read_text(encoding="utf-8"),
        )

    def _point_memory_path(self, kind: str, owner_id: str, filename: str) -> Path:
        folder = "agents" if kind == "agent" else "projects"
        return self.file_root / folder / _safe_segment(owner_id) / filename

    # ---- agent memory ------------------------------------------------

    def write_memory(
        self,
        *,
        content: str,
        agent_id: str,
        memory_type: str,
        run_id: str | None = None,
        project_id: str | None = None,
        tags: list[str] | None = None,
        importance: float = 1.0,
        confidence: float = 1.0,
        expires_at: str | None = None,
        metadata: dict | None = None,
    ) -> MemoryRecord:
        if memory_type not in VALID_MEMORY_TYPES:
            raise ValueError(f"memory_type must be one of {VALID_MEMORY_TYPES}")
        content = (content or "").strip()
        if not content:
            raise ValueError("memory content cannot be empty")
        self._validate_weight("importance", importance)
        self._validate_weight("confidence", confidence)

        tags = tags or []
        metadata = metadata or {}
        digest = _hash(content, agent_id, project_id or "", run_id or "", memory_type)

        existing = self._conn.execute(
            "SELECT * FROM memories WHERE hash = ?", (digest,)
        ).fetchone()
        if existing:
            return self._row_to_memory(existing)

        record = MemoryRecord(
            id=new_id("mem"),
            agent_id=agent_id,
            run_id=run_id,
            project_id=project_id,
            memory_type=memory_type,
            content=content,
            tags=tags,
            importance=importance,
            confidence=confidence,
            expires_at=expires_at,
            metadata=metadata,
        )
        self._conn.execute(
            """
            INSERT INTO memories (
                id, agent_id, run_id, project_id, memory_type, content, hash, tags,
                importance, confidence, expires_at, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.agent_id,
                record.run_id,
                record.project_id,
                record.memory_type,
                record.content,
                digest,
                json.dumps(tags, ensure_ascii=False),
                importance,
                confidence,
                expires_at,
                _json_dumps(metadata),
            ),
        )
        self._conn.commit()

        self.vector.upsert(
            AGENT_COLLECTION,
            record.id,
            record.content,
            {
                "agent_id": agent_id,
                "run_id": run_id,
                "project_id": project_id,
                "memory_type": memory_type,
                "tags": tags,
                "importance": importance,
                "confidence": confidence,
                "expires_at": expires_at,
                "metadata": metadata,
            },
        )
        return record

    def get_memory(self, memory_id: str, *, touch: bool = True) -> MemoryRecord | None:
        row = self._conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row and touch:
            self._touch_memories([memory_id])
            row = self._conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._row_to_memory(row) if row else None

    def search_memories(
        self,
        *,
        query: str,
        top_k: int = 10,
        memory_type: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
        since: str | None = None,
    ) -> list[MemoryRecord]:
        filters_sql, filters_params = self._memory_filters(
            memory_type, agent_id, project_id, run_id, since
        )

        bm25_rank = bm25_search(
            self._conn,
            table="memories",
            fts_table="memories_fts",
            query=query,
            top_k=max(top_k * 4, 20),
            where_sql=filters_sql,
            where_params=filters_params,
        )

        vector_payload_filter = {
            k: v
            for k, v in {
                "agent_id": agent_id,
                "run_id": run_id,
                "project_id": project_id,
                "memory_type": memory_type if memory_type != "all" else None,
            }.items()
            if v
        }
        vector_results = self.vector.query(
            AGENT_COLLECTION, query, max(top_k * 4, 20), vector_payload_filter or None
        )
        vector_rank = [(item["id"], item["score"]) for item in vector_results if item.get("id")]

        fused = reciprocal_rank_fusion(
            [bm25_rank, vector_rank], top_k=max(top_k * 4, 20)
        )

        if not fused:
            # No retrieval signal — fall back to recency for the requested filter set.
            sql = (
                "SELECT * FROM memories WHERE 1=1 "
                f"{('AND ' + filters_sql) if filters_sql else ''} "
                "ORDER BY (importance * confidence) DESC, created_at DESC LIMIT ?"
            )
            rows = self._conn.execute(sql, (*filters_params, top_k)).fetchall()
            records = [self._row_to_memory(row) for row in rows]
            self._touch_memories([record.id for record in records])
            return records

        records: list[MemoryRecord] = []
        score_map = dict(fused)
        for memory_id, _ in fused:
            sql = (
                "SELECT * FROM memories WHERE id = ? "
                f"{('AND ' + filters_sql) if filters_sql else ''}"
            )
            row = self._conn.execute(sql, (memory_id, *filters_params)).fetchone()
            if not row:
                continue
            record = self._row_to_memory(row)
            base_score = score_map.get(memory_id) or 0.0
            record.score = base_score * max(record.importance, 0.0) * max(record.confidence, 0.0)
            records.append(record)
        records.sort(key=lambda item: item.score or 0.0, reverse=True)
        records = records[:top_k]
        self._touch_memories([record.id for record in records])
        return records

    def list_memories(
        self,
        *,
        agent_id: str | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
        memory_type: str | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        filters_sql, filters_params = self._memory_filters(
            memory_type, agent_id, project_id, run_id, None
        )
        sql = (
            "SELECT * FROM memories WHERE 1=1 "
            f"{('AND ' + filters_sql) if filters_sql else ''} "
            "ORDER BY created_at DESC LIMIT ?"
        )
        rows = self._conn.execute(sql, (*filters_params, limit)).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def update_memory_weight(
        self,
        memory_id: str,
        *,
        importance: float | None = None,
        confidence: float | None = None,
        expires_at: str | None = None,
        metadata: dict | None = None,
    ) -> MemoryRecord | None:
        record = self.get_memory(memory_id, touch=False)
        if record is None:
            return None
        if importance is not None:
            self._validate_weight("importance", importance)
        if confidence is not None:
            self._validate_weight("confidence", confidence)

        next_metadata = record.metadata
        if metadata:
            next_metadata = {**next_metadata, **metadata}

        self._conn.execute(
            """
            UPDATE memories
               SET importance = COALESCE(?, importance),
                   confidence = COALESCE(?, confidence),
                   expires_at = ?,
                   metadata = ?
             WHERE id = ?
            """,
            (
                importance,
                confidence,
                expires_at if expires_at is not None else record.expires_at,
                _json_dumps(next_metadata),
                memory_id,
            ),
        )
        self._conn.commit()
        updated = self.get_memory(memory_id, touch=False)
        if updated is not None:
            self.vector.upsert(
                AGENT_COLLECTION,
                updated.id,
                updated.content,
                {
                    "agent_id": updated.agent_id,
                    "run_id": updated.run_id,
                    "project_id": updated.project_id,
                    "memory_type": updated.memory_type,
                    "tags": updated.tags,
                    "importance": updated.importance,
                    "confidence": updated.confidence,
                    "expires_at": updated.expires_at,
                    "metadata": updated.metadata,
                },
            )
        return updated

    def evict_memories(
        self,
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        memory_type: str | None = None,
        now: str | None = None,
        min_importance: float | None = None,
        max_records: int | None = None,
    ) -> list[str]:
        """Delete expired or low-weight memories in the requested scope.

        Expiration is explicit (`expires_at <= now`). Low-weight pruning is
        opt-in via `min_importance`. `max_records` keeps the highest weighted
        recent memories and evicts the rest inside the same filter scope.
        """
        filters_sql, filters_params = self._memory_filters(
            memory_type, agent_id, project_id, run_id, None
        )
        reason_clauses: list[str] = []
        reason_params: list = []
        if now:
            reason_clauses.append("expires_at IS NOT NULL AND expires_at <= ?")
            reason_params.append(now)
        if min_importance is not None:
            self._validate_weight("min_importance", min_importance)
            reason_clauses.append("importance < ?")
            reason_params.append(min_importance)

        ids: list[str] = []
        if reason_clauses:
            sql = (
                "SELECT id FROM memories WHERE 1=1 "
                f"{('AND ' + filters_sql) if filters_sql else ''} "
                f"AND ({' OR '.join(reason_clauses)})"
            )
            ids.extend(
                row["id"]
                for row in self._conn.execute(
                    sql, (*filters_params, *reason_params)
                ).fetchall()
            )

        if max_records is not None:
            if max_records < 0:
                raise ValueError("max_records cannot be negative")
            scope_sql = f"SELECT id FROM memories WHERE 1=1 {('AND ' + filters_sql) if filters_sql else ''} ORDER BY (importance * confidence) DESC, created_at DESC"
            scoped = [
                row["id"]
                for row in self._conn.execute(scope_sql, filters_params).fetchall()
            ]
            ids.extend(scoped[max_records:])

        unique_ids = sorted(set(ids))
        for memory_id in unique_ids:
            self.vector.delete(AGENT_COLLECTION, memory_id)
        if unique_ids:
            placeholders = ",".join("?" for _ in unique_ids)
            self._conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", tuple(unique_ids))
            self._conn.commit()
        return unique_ids

    # ---- short-term sessions ---------------------------------------

    def start_session(
        self,
        *,
        agent_id: str,
        run_id: str | None = None,
        project_id: str | None = None,
        input_summary: str | None = None,
        scratchpad: str | None = None,
        metadata: dict | None = None,
    ) -> AgentSessionCheckpoint:
        if not agent_id:
            raise ValueError("agent_id is required")
        run_id = run_id or new_id("run")
        existing = self.get_session(run_id)
        if existing is not None:
            return existing

        self._conn.execute(
            """
            INSERT INTO agent_sessions (
                run_id, agent_id, project_id, status, input_summary, scratchpad, metadata
            )
            VALUES (?, ?, ?, 'running', ?, ?, ?)
            """,
            (
                run_id,
                agent_id,
                project_id,
                input_summary,
                scratchpad,
                _json_dumps(metadata),
            ),
        )
        self._conn.commit()
        session = self.get_session(run_id)
        self.record_process_event(
            project_id=project_id,
            agent_id=agent_id,
            run_id=run_id,
            event_type="session_started",
            message=f"{agent_id} started run {run_id}",
            payload={"input_summary": input_summary, "metadata": metadata or {}},
        )
        return session

    def finish_session(
        self,
        *,
        run_id: str,
        status: str = "completed",
        output_summary: str | None = None,
        scratchpad: str | None = None,
        metadata: dict | None = None,
    ) -> AgentSessionCheckpoint:
        if status not in VALID_SESSION_STATUSES:
            raise ValueError(f"status must be one of {VALID_SESSION_STATUSES}")
        existing = self.get_session(run_id)
        if existing is None:
            raise KeyError(f"session not found: {run_id}")

        next_metadata = existing.metadata
        if metadata:
            next_metadata = {**next_metadata, **metadata}

        self._conn.execute(
            """
            UPDATE agent_sessions
               SET status = ?,
                   output_summary = COALESCE(?, output_summary),
                   scratchpad = COALESCE(?, scratchpad),
                   metadata = ?,
                   ended_at = CASE WHEN ? = 'running' THEN ended_at ELSE CURRENT_TIMESTAMP END
             WHERE run_id = ?
            """,
            (
                status,
                output_summary,
                scratchpad,
                _json_dumps(next_metadata),
                status,
                run_id,
            ),
        )
        self._conn.commit()
        session = self.get_session(run_id)
        self.record_process_event(
            project_id=session.project_id,
            agent_id=session.agent_id,
            run_id=run_id,
            event_type="session_finished",
            message=f"{session.agent_id} finished run {run_id} with status={status}",
            payload={
                "status": status,
                "output_summary": output_summary,
                "metadata": metadata or {},
            },
        )
        return session

    def get_session(self, run_id: str) -> AgentSessionCheckpoint | None:
        row = self._conn.execute(
            "SELECT * FROM agent_sessions WHERE run_id = ?", (run_id,)
        ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(
        self,
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[AgentSessionCheckpoint]:
        clauses: list[str] = []
        params: list = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        sql = (
            "SELECT * FROM agent_sessions WHERE 1=1 "
            f"{('AND ' + ' AND '.join(clauses)) if clauses else ''} "
            "ORDER BY started_at DESC LIMIT ?"
        )
        rows = self._conn.execute(sql, (*params, limit)).fetchall()
        return [self._row_to_session(row) for row in rows]

    def record_agent_run(
        self,
        *,
        agent_id: str,
        summary: dict,
        content: str,
        run_id: str | None = None,
        project_id: str | None = None,
        status: str = "completed",
    ) -> str:
        run_id = run_id or new_id("run")
        self.start_session(
            agent_id=agent_id,
            run_id=run_id,
            project_id=project_id,
            input_summary=f"{agent_id} run",
            metadata={"summary": summary},
        )
        self.finish_session(
            run_id=run_id,
            status=status,
            output_summary=content,
            metadata={"summary": summary},
        )
        self.write_memory(
            content=content,
            agent_id=agent_id,
            memory_type="episodic",
            run_id=run_id,
            project_id=project_id,
            tags=[agent_id, "run_summary"],
            importance=0.7,
            confidence=1.0,
            metadata={"summary": summary, "source": "agent_run"},
        )
        return run_id

    # ---- process memory --------------------------------------------

    def record_process_event(
        self,
        *,
        event_type: str,
        message: str,
        project_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        payload: dict | None = None,
    ) -> ProcessEvent:
        event_type = (event_type or "").strip()
        message = (message or "").strip()
        if not event_type:
            raise ValueError("event_type is required")
        if not message:
            message = event_type

        event_id = new_id("evt")
        self._conn.execute(
            """
            INSERT INTO process_events (
                id, project_id, agent_id, run_id, event_type, message, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                project_id,
                agent_id,
                run_id,
                event_type,
                message,
                _json_dumps(payload),
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM process_events WHERE id = ?", (event_id,)
        ).fetchone()
        return self._row_to_process_event(row)

    def list_process_events(
        self,
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        event_type: str | None = None,
        query: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[ProcessEvent]:
        clauses: list[str] = []
        params: list = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if query:
            clauses.append("(message LIKE ? OR payload LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        sql = (
            "SELECT * FROM process_events WHERE 1=1 "
            f"{('AND ' + ' AND '.join(clauses)) if clauses else ''} "
            "ORDER BY created_at DESC LIMIT ?"
        )
        rows = self._conn.execute(sql, (*params, limit)).fetchall()
        return [self._row_to_process_event(row) for row in rows]

    # ---- project context memory ------------------------------------

    def upsert_project_member(
        self,
        *,
        project_id: str,
        name: str,
        role: str,
        member_id: str | None = None,
        responsibility: str | None = None,
        metadata: dict | None = None,
    ) -> ProjectMember:
        if not project_id or not name or not role:
            raise ValueError("project_id, name, and role are required")
        member_id = member_id or name
        record_id = new_id("member")
        self._conn.execute(
            """
            INSERT INTO project_members (
                id, project_id, member_id, name, role, responsibility, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, member_id, role) DO UPDATE SET
                name = excluded.name,
                responsibility = excluded.responsibility,
                metadata = excluded.metadata,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                record_id,
                project_id,
                member_id,
                name,
                role,
                responsibility,
                _json_dumps(metadata),
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            """
            SELECT * FROM project_members
             WHERE project_id = ? AND member_id = ? AND role = ?
            """,
            (project_id, member_id, role),
        ).fetchone()
        return self._row_to_project_member(row)

    def list_project_members(self, project_id: str) -> list[ProjectMember]:
        rows = self._conn.execute(
            """
            SELECT * FROM project_members
             WHERE project_id = ?
             ORDER BY role, name
            """,
            (project_id,),
        ).fetchall()
        return [self._row_to_project_member(row) for row in rows]

    def upsert_project_artifact(
        self,
        *,
        project_id: str,
        artifact_type: str,
        name: str,
        external_id: str | None = None,
        url: str | None = None,
        metadata: dict | None = None,
    ) -> ProjectArtifact:
        if not project_id or not artifact_type or not name:
            raise ValueError("project_id, artifact_type, and name are required")
        normalized_external_id = external_id or ""
        record_id = new_id("artifact")
        self._conn.execute(
            """
            INSERT INTO project_artifacts (
                id, project_id, artifact_type, name, external_id, url, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, artifact_type, name, external_id) DO UPDATE SET
                url = excluded.url,
                metadata = excluded.metadata,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                record_id,
                project_id,
                artifact_type,
                name,
                normalized_external_id,
                url,
                _json_dumps(metadata),
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            """
            SELECT * FROM project_artifacts
             WHERE project_id = ? AND artifact_type = ? AND name = ? AND external_id = ?
            """,
            (project_id, artifact_type, name, normalized_external_id),
        ).fetchone()
        return self._row_to_project_artifact(row)

    def list_project_artifacts(
        self, project_id: str, artifact_type: str | None = None
    ) -> list[ProjectArtifact]:
        if artifact_type:
            rows = self._conn.execute(
                """
                SELECT * FROM project_artifacts
                 WHERE project_id = ? AND artifact_type = ?
                 ORDER BY artifact_type, name
                """,
                (project_id, artifact_type),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM project_artifacts
                 WHERE project_id = ?
                 ORDER BY artifact_type, name
                """,
                (project_id,),
            ).fetchall()
        return [self._row_to_project_artifact(row) for row in rows]

    def get_project_context(self, project_id: str) -> dict:
        profile = self.read_project_profile(project_id)
        return {
            "project_id": project_id,
            "profile": profile.to_dict() if profile else None,
            "members": [item.to_dict() for item in self.list_project_members(project_id)],
            "artifacts": [item.to_dict() for item in self.list_project_artifacts(project_id)],
            "recent_events": [
                item.to_dict()
                for item in self.list_process_events(project_id=project_id, limit=20)
            ],
        }

    def sync_project_context(self, state: dict) -> dict:
        projects = state.get("projects", [])
        tasks = state.get("tasks", [])
        milestones = state.get("milestones", [])
        reports = state.get("weekly_reports", [])
        retrospectives = state.get("retrospectives", [])

        synced = {"projects": 0, "members": 0, "artifacts": 0}
        for project in projects:
            project_id = project["id"]
            self.ensure_project_profile(
                project_id=project_id,
                content=self._render_project_profile(project),
            )
            synced["projects"] += 1

            if project.get("owner"):
                self.upsert_project_member(
                    project_id=project_id,
                    name=project["owner"],
                    role="project_owner",
                    responsibility=f"负责 {project.get('name', project_id)} 项目推进",
                    metadata={"source": "project.owner"},
                )
                synced["members"] += 1

            tasks_by_owner: dict[str, list[dict]] = {}
            for task in [
                item for item in tasks if item.get("project_id") == project_id and item.get("owner")
            ]:
                tasks_by_owner.setdefault(task["owner"], []).append(task)
            for owner, owner_tasks in tasks_by_owner.items():
                task_names = "、".join(item.get("name", item["id"]) for item in owner_tasks)
                self.upsert_project_member(
                    project_id=project_id,
                    name=owner,
                    role="task_owner",
                    responsibility=f"任务: {task_names}",
                    metadata={
                        "source": "task",
                        "task_ids": [item["id"] for item in owner_tasks],
                        "statuses": {item["id"]: item.get("status") for item in owner_tasks},
                    },
                )
                synced["members"] += 1

            milestones_by_owner: dict[str, list[dict]] = {}
            for milestone in [
                item for item in milestones if item.get("project_id") == project_id and item.get("owner")
            ]:
                milestones_by_owner.setdefault(milestone["owner"], []).append(milestone)
            for owner, owner_milestones in milestones_by_owner.items():
                milestone_names = "、".join(
                    item.get("name", item["id"]) for item in owner_milestones
                )
                self.upsert_project_member(
                    project_id=project_id,
                    name=owner,
                    role="milestone_owner",
                    responsibility=f"里程碑: {milestone_names}",
                    metadata={
                        "source": "milestone",
                        "milestone_ids": [item["id"] for item in owner_milestones],
                        "statuses": {item["id"]: item.get("status") for item in owner_milestones},
                    },
                )
                synced["members"] += 1

            if project.get("latest_weekly_report"):
                self.upsert_project_artifact(
                    project_id=project_id,
                    artifact_type="weekly_report",
                    name=Path(project["latest_weekly_report"]).name,
                    url=project["latest_weekly_report"],
                    metadata={"source": "project.latest_weekly_report"},
                )
                synced["artifacts"] += 1

            for report in [item for item in reports if item.get("project_id") == project_id]:
                self.upsert_project_artifact(
                    project_id=project_id,
                    artifact_type="weekly_report",
                    name=Path(report["document_path"]).name,
                    external_id=report.get("id"),
                    url=report.get("document_path"),
                    metadata={"period": report.get("period"), "send_status": report.get("send_status")},
                )
                synced["artifacts"] += 1

            for retro in [item for item in retrospectives if item.get("project_id") == project_id]:
                self.upsert_project_artifact(
                    project_id=project_id,
                    artifact_type="retrospective",
                    name=Path(retro["document_path"]).name,
                    external_id=retro.get("id"),
                    url=retro.get("document_path"),
                    metadata={"generated_on": retro.get("generated_on")},
                )
                synced["artifacts"] += 1

            self.record_process_event(
                project_id=project_id,
                event_type="project_context_synced",
                message=f"project context synced for {project_id}",
                payload={"project": project, "counts": synced},
            )
        return synced

    @staticmethod
    def _render_project_profile(project: dict) -> str:
        lines = [
            f"# {project.get('name', project.get('id', 'Project'))}",
            "",
            f"- project_id: {project.get('id', '')}",
            f"- owner: {project.get('owner', '')}",
            f"- status: {project.get('status', '')}",
            f"- priority: {project.get('priority', '')}",
            f"- target_launch_date: {project.get('target_launch_date', '')}",
            f"- health: {project.get('health', '')}",
            f"- risk_level: {project.get('risk_level', '')}",
            "",
            "## Operating Notes",
            "- This PROJECT.md is point memory for stable project context.",
            "- The main/project-owning agent may update it when stable project facts change.",
        ]
        return "\n".join(lines)

    # ---- user documents ---------------------------------------------

    def ingest_document(
        self,
        *,
        file_path: Path,
        source_type: str,
        corpus_id: str,
        project_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        uploaded_by: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        if source_type not in VALID_SOURCE_TYPES:
            raise ValueError(f"source_type must be one of {VALID_SOURCE_TYPES}")
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(file_path)

        text = read_text_file(path)
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError(f"no extractable content in {file_path}")

        document_id = new_id("doc")
        self._conn.execute(
            """
            INSERT INTO documents (
                id, corpus_id, project_id, agent_id, run_id, filename, source_type,
                chunk_count, uploaded_by, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                corpus_id,
                project_id,
                agent_id,
                run_id,
                path.name,
                source_type,
                len(chunks),
                uploaded_by,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )

        chunk_records: list[DocumentChunk] = []
        for idx, chunk_content in enumerate(chunks):
            chunk_id = new_id("chk")
            digest = _hash(chunk_content, document_id, str(idx))
            self._conn.execute(
                """
                INSERT INTO doc_chunks (
                    id, document_id, corpus_id, project_id, agent_id, run_id,
                    source_type, chunk_index, content, hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    document_id,
                    corpus_id,
                    project_id,
                    agent_id,
                    run_id,
                    source_type,
                    idx,
                    chunk_content,
                    digest,
                ),
            )
            chunk_records.append(
                DocumentChunk(
                    id=chunk_id,
                    document_id=document_id,
                    corpus_id=corpus_id,
                    project_id=project_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    source_type=source_type,
                    chunk_index=idx,
                    content=chunk_content,
                )
            )

        self._conn.commit()

        for chunk in chunk_records:
            self.vector.upsert(
                DOC_COLLECTION,
                chunk.id,
                chunk.content,
                {
                    "document_id": document_id,
                    "corpus_id": corpus_id,
                    "project_id": project_id,
                    "agent_id": agent_id,
                    "run_id": run_id,
                    "source_type": source_type,
                    "chunk_index": chunk.chunk_index,
                    "filename": path.name,
                },
            )

        return {
            "document_id": document_id,
            "filename": path.name,
            "corpus_id": corpus_id,
            "project_id": project_id,
            "agent_id": agent_id,
            "run_id": run_id,
            "source_type": source_type,
            "chunk_count": len(chunk_records),
        }

    def search_documents(
        self,
        *,
        query: str,
        top_k: int = 5,
        corpus_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        source_type: str | None = None,
    ) -> list[DocumentChunk]:
        filters_sql, filters_params = self._chunk_filters(
            corpus_id, source_type, project_id, agent_id, run_id
        )

        bm25_rank = bm25_search(
            self._conn,
            table="doc_chunks",
            fts_table="chunks_fts",
            query=query,
            top_k=max(top_k * 4, 20),
            where_sql=filters_sql,
            where_params=filters_params,
        )

        vector_filter = {
            k: v
            for k, v in {
                "corpus_id": corpus_id,
                "project_id": project_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "source_type": source_type if source_type != "all" else None,
            }.items()
            if v
        }
        vector_results = self.vector.query(
            DOC_COLLECTION, query, max(top_k * 4, 20), vector_filter or None
        )
        vector_rank = [(item["id"], item["score"]) for item in vector_results if item.get("id")]

        fused = reciprocal_rank_fusion([bm25_rank, vector_rank], top_k=top_k)

        score_map = dict(fused)
        records: list[DocumentChunk] = []
        for chunk_id, _ in fused:
            sql = (
                "SELECT * FROM doc_chunks WHERE id = ? "
                f"{('AND ' + filters_sql) if filters_sql else ''}"
            )
            row = self._conn.execute(sql, (chunk_id, *filters_params)).fetchone()
            if not row:
                continue
            chunk = self._row_to_chunk(row)
            chunk.score = score_map.get(chunk_id)
            records.append(chunk)
        return records

    def list_documents(
        self, corpus_id: str | None = None, project_id: str | None = None
    ) -> list[dict]:
        clauses: list[str] = []
        params: list = []
        if corpus_id:
            clauses.append("corpus_id = ?")
            params.append(corpus_id)
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        sql = (
            "SELECT * FROM documents WHERE 1=1 "
            f"{('AND ' + ' AND '.join(clauses)) if clauses else ''} "
            "ORDER BY created_at DESC"
        )
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    # ---- helpers -----------------------------------------------------

    @staticmethod
    def _memory_filters(
        memory_type: str | None,
        agent_id: str | None,
        project_id: str | None,
        run_id: str | None,
        since: str | None,
    ) -> tuple[str, tuple]:
        clauses: list[str] = []
        params: list = []
        if memory_type and memory_type != "all":
            clauses.append("memory_type = ?")
            params.append(memory_type)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        return (" AND ".join(clauses), tuple(params))

    @staticmethod
    def _chunk_filters(
        corpus_id: str | None,
        source_type: str | None,
        project_id: str | None,
        agent_id: str | None,
        run_id: str | None,
    ) -> tuple[str, tuple]:
        clauses: list[str] = []
        params: list = []
        if corpus_id:
            clauses.append("corpus_id = ?")
            params.append(corpus_id)
        if source_type and source_type != "all":
            clauses.append("source_type = ?")
            params.append(source_type)
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        return (" AND ".join(clauses), tuple(params))

    @staticmethod
    def _validate_weight(name: str, value: float) -> None:
        if not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be numeric")
        if value < 0:
            raise ValueError(f"{name} cannot be negative")

    def _touch_memories(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        placeholders = ",".join("?" for _ in memory_ids)
        self._conn.execute(
            f"""
            UPDATE memories
               SET access_count = access_count + 1,
                   last_accessed = CURRENT_TIMESTAMP
             WHERE id IN ({placeholders})
            """,
            tuple(memory_ids),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> MemoryRecord:
        keys = set(row.keys())
        tags = _json_loads(row["tags"], [])
        return MemoryRecord(
            id=row["id"],
            agent_id=row["agent_id"],
            run_id=row["run_id"],
            project_id=row["project_id"],
            memory_type=row["memory_type"],
            content=row["content"],
            tags=tags,
            importance=float(row["importance"]) if "importance" in keys else 1.0,
            confidence=float(row["confidence"]) if "confidence" in keys else 1.0,
            access_count=int(row["access_count"]) if "access_count" in keys else 0,
            last_accessed=row["last_accessed"] if "last_accessed" in keys else None,
            expires_at=row["expires_at"] if "expires_at" in keys else None,
            metadata=_json_loads(row["metadata"], {}) if "metadata" in keys else {},
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> DocumentChunk:
        keys = set(row.keys())
        return DocumentChunk(
            id=row["id"],
            document_id=row["document_id"],
            corpus_id=row["corpus_id"],
            project_id=row["project_id"] if "project_id" in keys else None,
            agent_id=row["agent_id"] if "agent_id" in keys else None,
            run_id=row["run_id"] if "run_id" in keys else None,
            source_type=row["source_type"],
            chunk_index=row["chunk_index"],
            content=row["content"],
        )

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> AgentSessionCheckpoint:
        return AgentSessionCheckpoint(
            run_id=row["run_id"],
            agent_id=row["agent_id"],
            project_id=row["project_id"],
            status=row["status"],
            input_summary=row["input_summary"],
            output_summary=row["output_summary"],
            scratchpad=row["scratchpad"],
            metadata=_json_loads(row["metadata"], {}),
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )

    @staticmethod
    def _row_to_process_event(row: sqlite3.Row) -> ProcessEvent:
        return ProcessEvent(
            id=row["id"],
            project_id=row["project_id"],
            agent_id=row["agent_id"],
            run_id=row["run_id"],
            event_type=row["event_type"],
            message=row["message"],
            payload=_json_loads(row["payload"], {}),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_project_member(row: sqlite3.Row) -> ProjectMember:
        return ProjectMember(
            id=row["id"],
            project_id=row["project_id"],
            member_id=row["member_id"],
            name=row["name"],
            role=row["role"],
            responsibility=row["responsibility"],
            metadata=_json_loads(row["metadata"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_project_artifact(row: sqlite3.Row) -> ProjectArtifact:
        external_id = row["external_id"] or None
        return ProjectArtifact(
            id=row["id"],
            project_id=row["project_id"],
            artifact_type=row["artifact_type"],
            name=row["name"],
            external_id=external_id,
            url=row["url"],
            metadata=_json_loads(row["metadata"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
