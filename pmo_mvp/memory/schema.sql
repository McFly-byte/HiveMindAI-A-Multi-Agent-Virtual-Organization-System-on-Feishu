-- Memory metadata store. Vector data lives in mem0/Qdrant when available;
-- this database mirrors the payload so we can filter, dedup, and run BM25
-- without depending on an external service.

CREATE TABLE IF NOT EXISTS memories (
    id           TEXT PRIMARY KEY,
    agent_id     TEXT NOT NULL,
    run_id       TEXT,
    project_id   TEXT,
    memory_type  TEXT NOT NULL CHECK(memory_type IN ('episodic','reflective','procedural')),
    content      TEXT NOT NULL,
    hash         TEXT NOT NULL UNIQUE,
    tags         TEXT,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memories_agent     ON memories(agent_id);
CREATE INDEX IF NOT EXISTS idx_memories_project   ON memories(project_id);
CREATE INDEX IF NOT EXISTS idx_memories_type      ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_created   ON memories(created_at);

CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    corpus_id    TEXT NOT NULL,
    filename     TEXT NOT NULL,
    source_type  TEXT NOT NULL CHECK(source_type IN ('knowledge','instruction','domain_data')),
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    uploaded_by  TEXT,
    metadata     TEXT,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_corpus ON documents(corpus_id);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_type);

CREATE TABLE IF NOT EXISTS doc_chunks (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    corpus_id    TEXT NOT NULL,
    source_type  TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    content      TEXT NOT NULL,
    hash         TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc    ON doc_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_corpus ON doc_chunks(corpus_id);

-- FTS5 indices for keyword-based BM25 retrieval. These are the
-- fallback retrieval path when no vector backend is configured.
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='rowid',
    tokenize='trigram'
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content,
    content='doc_chunks',
    content_rowid='rowid',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON doc_chunks BEGIN
    INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON doc_chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES('delete', old.rowid, old.content);
END;
