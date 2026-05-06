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
| 上下文管理 | `agent_runtime.context.AgentContext` | 上下文预算、scratchpad、memory 检索和 compact summary。 |
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

## AgentSession 使用方式

运行态 `AgentSession` 只在 agent loop 内存中流转：

1. `AgentRuntime.create_session()` 创建 `agent_runtime.session.AgentSession`。
2. Handler 执行时把同一个 `session` 传给 `tool_executor.call_tool()`。
3. `ToolIntegrationExecutor` 在 `session.steps` 中追加 `ToolCallRecord`。
4. Handler 返回 Pydantic output 后，`AgentRuntime` 把 output 摘要写入 `session.memory`。
5. `MemoryTraceSink` 在 start/end/error 时把 session 摘要持久化成 `AgentSessionCheckpoint`。

示例：

```python
session = runtime.create_session(event, "project_secretary")

await tool_executor.call_tool(
    "memory_write",
    {
        "content": "阻塞任务需要记录 owner、解除时间和下一步动作。",
        "memory_type": "procedural",
        "agent_id": str(session.agent_name),
        "project_id": session.project_id,
        "run_id": session.run_id,
    },
    session,
)
```

不要在 agent loop 中每次读取短期状态都查 SQLite。短期热状态应读写当前
`AgentSession` 对象；SQLite 里的 `AgentSessionCheckpoint` 用于跨 run 查询、
审计、恢复和报表，不承担当前 run 的高频上下文管理。

## Compact Summary 边界

compact summary 不由 `MemoryStore` 自行生成，已经放在
`agent_runtime.context.AgentContext`：

| 层 | 职责 |
| --- | --- |
| `AgentSession` | 保存当前 run 的热状态、steps、临时 memory。 |
| `AgentContext` | 判断上下文是否超长，裁剪 messages/tool results，生成 compact summary。 |
| `MemoryTraceSink` / `MemoryStore` | 持久化 compact 后的 summary、scratchpad、checkpoint 和长期记忆。 |

也就是说，compact 策略属于 runtime context 层；memory 层只负责可靠存取和检索。
当前默认策略是 deterministic summarizer，后续可以替换为 LLM summarizer。

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
