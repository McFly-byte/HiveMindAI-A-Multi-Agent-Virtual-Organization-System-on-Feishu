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
    importance   REAL NOT NULL DEFAULT 1.0,
    confidence   REAL NOT NULL DEFAULT 1.0,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed TEXT,
    expires_at   TEXT,
    metadata     TEXT,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memories_agent     ON memories(agent_id);
CREATE INDEX IF NOT EXISTS idx_memories_project   ON memories(project_id);
CREATE INDEX IF NOT EXISTS idx_memories_run       ON memories(run_id);
CREATE INDEX IF NOT EXISTS idx_memories_type      ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_created   ON memories(created_at);

CREATE TABLE IF NOT EXISTS agent_sessions (
    run_id        TEXT PRIMARY KEY,
    agent_id      TEXT NOT NULL,
    project_id    TEXT,
    status        TEXT NOT NULL DEFAULT 'running'
                  CHECK(status IN ('running','completed','failed','cancelled')),
    input_summary TEXT,
    output_summary TEXT,
    scratchpad    TEXT,
    metadata      TEXT,
    started_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_agent   ON agent_sessions(agent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON agent_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status  ON agent_sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON agent_sessions(started_at);

CREATE TABLE IF NOT EXISTS process_events (
    id          TEXT PRIMARY KEY,
    project_id  TEXT,
    agent_id    TEXT,
    run_id      TEXT,
    event_type  TEXT NOT NULL,
    message     TEXT NOT NULL,
    payload     TEXT,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_process_project ON process_events(project_id);
CREATE INDEX IF NOT EXISTS idx_process_agent   ON process_events(agent_id);
CREATE INDEX IF NOT EXISTS idx_process_run     ON process_events(run_id);
CREATE INDEX IF NOT EXISTS idx_process_type    ON process_events(event_type);
CREATE INDEX IF NOT EXISTS idx_process_created ON process_events(created_at);

CREATE TABLE IF NOT EXISTS project_members (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    member_id      TEXT NOT NULL,
    name           TEXT NOT NULL,
    role           TEXT NOT NULL,
    responsibility TEXT,
    metadata       TEXT,
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, member_id, role)
);

CREATE INDEX IF NOT EXISTS idx_members_project ON project_members(project_id);
CREATE INDEX IF NOT EXISTS idx_members_member  ON project_members(member_id);

CREATE TABLE IF NOT EXISTS project_artifacts (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    name          TEXT NOT NULL,
    external_id   TEXT,
    url           TEXT,
    metadata      TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, artifact_type, name, external_id)
);

CREATE INDEX IF NOT EXISTS idx_artifacts_project ON project_artifacts(project_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_type    ON project_artifacts(artifact_type);

CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    corpus_id    TEXT NOT NULL,
    project_id   TEXT,
    agent_id     TEXT,
    run_id       TEXT,
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
    project_id   TEXT,
    agent_id     TEXT,
    run_id       TEXT,
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
