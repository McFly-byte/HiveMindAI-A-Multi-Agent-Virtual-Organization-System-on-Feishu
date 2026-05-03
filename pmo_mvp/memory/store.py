from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .doc_pipeline import chunk_text, read_text_file
from .embedding import VectorBackend, build_backend
from .retrieval import bm25_search, reciprocal_rank_fusion
from ..utils import new_id


SCHEMA_PATH = Path(__file__).with_name("schema.sql")

AGENT_COLLECTION = "agent_memories"
DOC_COLLECTION = "user_documents"

VALID_MEMORY_TYPES = {"episodic", "reflective", "procedural"}
VALID_SOURCE_TYPES = {"knowledge", "instruction", "domain_data"}


@dataclass
class MemoryRecord:
    id: str
    agent_id: str
    run_id: str | None
    project_id: str | None
    memory_type: str
    content: str
    tags: list[str] = field(default_factory=list)
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
    score: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "corpus_id": self.corpus_id,
            "source_type": self.source_type,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "score": self.score,
        }


def _hash(content: str, *salts: str) -> str:
    h = hashlib.sha256()
    h.update(content.encode("utf-8"))
    for salt in salts:
        h.update(b"\x1f")
        h.update((salt or "").encode("utf-8"))
    return h.hexdigest()


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
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()
        self.vector = vector_backend if vector_backend is not None else build_backend()

    # ---- lifecycle ---------------------------------------------------

    def _init_schema(self) -> None:
        ddl = SCHEMA_PATH.read_text(encoding="utf-8")
        self._conn.executescript(ddl)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

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
    ) -> MemoryRecord:
        if memory_type not in VALID_MEMORY_TYPES:
            raise ValueError(f"memory_type must be one of {VALID_MEMORY_TYPES}")
        content = (content or "").strip()
        if not content:
            raise ValueError("memory content cannot be empty")

        tags = tags or []
        digest = _hash(content, agent_id, project_id or "", memory_type)

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
        )
        self._conn.execute(
            """
            INSERT INTO memories (id, agent_id, run_id, project_id, memory_type, content, hash, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            },
        )
        return record

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
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
        since: str | None = None,
    ) -> list[MemoryRecord]:
        filters_sql, filters_params = self._memory_filters(memory_type, agent_id, project_id, since)

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
                "project_id": project_id,
                "memory_type": memory_type,
            }.items()
            if v
        }
        vector_results = self.vector.query(
            AGENT_COLLECTION, query, max(top_k * 4, 20), vector_payload_filter or None
        )
        vector_rank = [(item["id"], item["score"]) for item in vector_results if item.get("id")]

        fused = reciprocal_rank_fusion([bm25_rank, vector_rank], top_k=top_k)

        if not fused:
            # No retrieval signal — fall back to recency for the requested filter set.
            sql = f"SELECT * FROM memories WHERE 1=1 {('AND ' + filters_sql) if filters_sql else ''} ORDER BY created_at DESC LIMIT ?"
            rows = self._conn.execute(sql, (*filters_params, top_k)).fetchall()
            return [self._row_to_memory(row) for row in rows]

        records: list[MemoryRecord] = []
        score_map = dict(fused)
        for memory_id, _ in fused:
            row = self._conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if not row:
                continue
            record = self._row_to_memory(row)
            record.score = score_map.get(memory_id)
            records.append(record)
        return records

    def list_memories(
        self,
        *,
        agent_id: str | None = None,
        project_id: str | None = None,
        memory_type: str | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        filters_sql, filters_params = self._memory_filters(memory_type, agent_id, project_id, None)
        sql = f"SELECT * FROM memories WHERE 1=1 {('AND ' + filters_sql) if filters_sql else ''} ORDER BY created_at DESC LIMIT ?"
        rows = self._conn.execute(sql, (*filters_params, limit)).fetchall()
        return [self._row_to_memory(row) for row in rows]

    # ---- user documents ---------------------------------------------

    def ingest_document(
        self,
        *,
        file_path: Path,
        source_type: str,
        corpus_id: str,
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
            INSERT INTO documents (id, corpus_id, filename, source_type, chunk_count, uploaded_by, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                corpus_id,
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
                INSERT INTO doc_chunks (id, document_id, corpus_id, source_type, chunk_index, content, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (chunk_id, document_id, corpus_id, source_type, idx, chunk_content, digest),
            )
            chunk_records.append(
                DocumentChunk(
                    id=chunk_id,
                    document_id=document_id,
                    corpus_id=corpus_id,
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
                    "source_type": source_type,
                    "chunk_index": chunk.chunk_index,
                    "filename": path.name,
                },
            )

        return {
            "document_id": document_id,
            "filename": path.name,
            "corpus_id": corpus_id,
            "source_type": source_type,
            "chunk_count": len(chunk_records),
        }

    def search_documents(
        self,
        *,
        query: str,
        top_k: int = 5,
        corpus_id: str | None = None,
        source_type: str | None = None,
    ) -> list[DocumentChunk]:
        filters_sql, filters_params = self._chunk_filters(corpus_id, source_type)

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
            for k, v in {"corpus_id": corpus_id, "source_type": source_type}.items()
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
            row = self._conn.execute("SELECT * FROM doc_chunks WHERE id = ?", (chunk_id,)).fetchone()
            if not row:
                continue
            chunk = self._row_to_chunk(row)
            chunk.score = score_map.get(chunk_id)
            records.append(chunk)
        return records

    def list_documents(self, corpus_id: str | None = None) -> list[dict]:
        if corpus_id:
            rows = self._conn.execute(
                "SELECT * FROM documents WHERE corpus_id = ? ORDER BY created_at DESC", (corpus_id,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM documents ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    # ---- helpers -----------------------------------------------------

    @staticmethod
    def _memory_filters(
        memory_type: str | None,
        agent_id: str | None,
        project_id: str | None,
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
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        return (" AND ".join(clauses), tuple(params))

    @staticmethod
    def _chunk_filters(
        corpus_id: str | None, source_type: str | None
    ) -> tuple[str, tuple]:
        clauses: list[str] = []
        params: list = []
        if corpus_id:
            clauses.append("corpus_id = ?")
            params.append(corpus_id)
        if source_type and source_type != "all":
            clauses.append("source_type = ?")
            params.append(source_type)
        return (" AND ".join(clauses), tuple(params))

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> MemoryRecord:
        tags = json.loads(row["tags"]) if row["tags"] else []
        return MemoryRecord(
            id=row["id"],
            agent_id=row["agent_id"],
            run_id=row["run_id"],
            project_id=row["project_id"],
            memory_type=row["memory_type"],
            content=row["content"],
            tags=tags,
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> DocumentChunk:
        return DocumentChunk(
            id=row["id"],
            document_id=row["document_id"],
            corpus_id=row["corpus_id"],
            source_type=row["source_type"],
            chunk_index=row["chunk_index"],
            content=row["content"],
        )
