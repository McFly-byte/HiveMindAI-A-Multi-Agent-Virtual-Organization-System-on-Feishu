# Agent Runtime Memory

本模块是正式 `src/agent_runtime` 架构下的记忆层。它不再定义运行态
`AgentSession`；真实 agent loop 的 session 只来自
`agent_runtime.session.AgentSession`。本模块负责持久化 checkpoint、过程日志、
长期记忆和文档检索，并通过 `tool_integration` 暴露为工具。

## 分层

| 层级 | 正式位置 | 职责 |
| --- | --- | --- |
| 点记忆 | `agents/*/AGENT.md`、`projects/*/PROJECT.md` | Agent prompt 和项目稳定上下文。 |
| 短期记忆 | `agent_runtime.session.AgentSession` | run 内热状态：steps、tool calls、errors、SessionMemoryItem。 |
| 短期 checkpoint | `agent_runtime.memory.AgentSessionCheckpoint` | SQLite 中的持久化摘要，用于恢复和审计。 |
| 过程记忆 | `process_events`、`session.steps`、`traces/*.jsonl` | 关键过程日志和工具调用轨迹。 |
| 长期记忆 | `memories` + FTS5 + 可选向量后端 | 跨 run 可复用的 episodic / reflective / procedural 经验。 |
| 文档记忆 | `documents` / `doc_chunks` + FTS5 | 用户文档 chunk 检索。 |

## 正式接入点

### Runtime checkpoint

`agent_runtime.memory.trace_sink.MemoryTraceSink` 会被
`agent_runtime.mvp.builder.build_runtime_with_tool_integration()` 组装进
`CompositeTraceSink`：

```text
AgentRuntime.run_agent()
  ├─ create src.agent_runtime.session.AgentSession
  ├─ CompositeTraceSink.on_session_start()
  │    ├─ LocalJsonlTraceSink -> traces/<run_id>.jsonl
  │    └─ MemoryTraceSink -> runtime/memory.db agent_sessions
  ├─ handler.run(session, payload)
  └─ CompositeTraceSink.on_session_end()
       ├─ LocalJsonlTraceSink -> full session snapshot
       └─ MemoryTraceSink -> checkpoint + scratchpad + metadata
```

### Tool integration

`tool_integrations/memory_tools.py` 将 `MemoryToolset` 注册进正式
`ToolRegistry`。这些工具会经过 `ToolIntegrationExecutor` 的 policy 校验，并被
记录到 `session.steps` 中。

默认数据库路径：

```text
runtime/memory.db
```

可通过环境变量覆盖：

```bash
export HIVEMIND_MEMORY_DB_PATH=/path/to/memory.db
```

## 暴露工具

| 工具组 | 工具 |
| --- | --- |
| 长期记忆 | `memory_write`、`memory_search`、`memory_get`、`memory_reflect`、`memory_weight_update`、`memory_evict` |
| 点记忆 | `profile_write`、`profile_read` |
| checkpoint | `session_start`、`session_finish`、`session_get`、`session_list` |
| 过程记忆 | `process_log`、`process_search` |
| 项目上下文 | `project_context_upsert`、`project_context_get` |
| 文档 | `doc_ingest`、`doc_search` |

注意：`session_start` / `session_finish` 在正式架构里只表示“持久化 checkpoint”
工具，不创建或驱动运行态 `AgentSession`。运行态 session 必须由
`AgentRuntime.create_session()` 创建。

## AgentSession 与 checkpoint 的关系

```text
agent_runtime.session.AgentSession          # live object, hot loop state
        │
        ├─ session.steps                    # tool calls / llm calls / errors
        ├─ session.memory                   # run 内短期记忆
        └─ MemoryTraceSink
              └─ AgentSessionCheckpoint     # durable summary in SQLite
```

`AgentSessionCheckpoint` 字段：

- `run_id`
- `agent_id`
- `project_id`
- `status`
- `input_summary`
- `output_summary`
- `scratchpad`
- `metadata`
- `started_at`
- `ended_at`

## 直接使用 MemoryStore

```python
from pathlib import Path
from agent_runtime.memory import MemoryStore, NullVectorBackend

store = MemoryStore(
    db_path=Path("runtime/memory.db"),
    vector_backend=NullVectorBackend(),
)

record = store.write_memory(
    content="风险分析发现阻塞任务应先确认 owner 和解除时间。",
    agent_id="risk_analysis",
    project_id="enterprise_rag",
    run_id="run-123",
    memory_type="procedural",
    importance=1.2,
    tags=["risk", "blocked_task"],
)

results = store.search_memories(
    query="阻塞任务如何处理",
    agent_id="risk_analysis",
    project_id="enterprise_rag",
    top_k=5,
)

store.close()
```

## 通过 ToolExecutor 使用

Agent handler 不应直接绕过工具体系调用 memory。正式路径是：

```python
await tool_executor.call_tool(
    "memory_search",
    {
        "query": "阻塞任务风险",
        "agent_id": str(session.agent_name),
        "project_id": session.project_id,
        "memory_type": "all",
        "top_k": 5,
    },
    session,
)
```

这样工具调用会被写入 `session.steps`，同时遵守 `agents/*/agent.yaml` 中的
`tool_policy.allowed_tools`。

## Schema

核心表：

```sql
memories
agent_sessions
process_events
project_members
project_artifacts
documents
doc_chunks
memories_fts
chunks_fts
```

长期记忆支持：

- `project_id` / `agent_id` / `run_id` 过滤
- `importance` / `confidence` 加权
- `access_count` / `last_accessed` 访问统计
- `expires_at` 和 `memory_evict` 淘汰

