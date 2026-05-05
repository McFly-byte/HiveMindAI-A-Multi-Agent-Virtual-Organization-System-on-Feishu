# PMO 记忆系统

为 PMO 多 Agent 系统提供四层记忆：点记忆、短期记忆、过程记忆、长期记忆。
Agent 通过 `MemoryToolset` 或 `MemoryStore` 读写记忆，而不是直接访问底层存储。
用户上传文档与 Agent 自己沉淀的经验仍共享同一套混合检索通道。

## 设计目标

- **四层记忆并存**：点记忆（`AGENT.md` / `PROJECT.md`）+ 短期记忆
  （`AgentSession`）+ 过程记忆（项目日志、成员、产物）+ 长期记忆
  （按项目 / Agent / Run 过滤的经验与文档 chunk）。
- **工具化访问**：对 LLM 暴露记忆读写、反思、profile、session、process、
  project context、权重 / 淘汰、文档检索等工具，每个工具自带 JSON Schema。
- **可选向量化**：默认使用 SQLite FTS5 + BM25 跑通，无需任何外部依赖；安装
  `mem0ai` 后自动切换到向量 + BM25 RRF 融合检索。
- **MVP 友好**：不引入服务进程，所有持久化都落在 `runtime/memory.db`。

## 模块结构

```text
pmo_mvp/memory/
├── __init__.py        # 公共导出：MemoryStore / MemoryToolset / VectorBackend
├── schema.sql         # SQLite DDL：memories / sessions / process / context / docs + FTS5
├── store.py           # MemoryStore：四层记忆写入、检索、文档摄取的核心实现
├── embedding.py       # VectorBackend 协议：NullBackend（默认）/ Mem0Backend（可选）
├── retrieval.py       # BM25 over FTS5 + RRF 融合 + CJK trigram 滑窗
├── doc_pipeline.py    # 段落感知切块器（默认 1600 chars，overlap 200）
└── tools.py           # MemoryToolset：记忆工具 schema 与 dispatcher
```

## 技术架构

记忆系统由三层组成：文件化点记忆、SQLite 元数据/全文检索、可选向量后端。
调用方只依赖 `MemoryStore` 或 `MemoryToolset`，底层存储细节被封装在
`store.py` 内。

```text
Agent / LLM Runtime / CLI
        │
        ├─► MemoryToolset.schemas()   # 暴露 JSON Schema 给工具调用模型
        ├─► MemoryToolset.dispatch()  # 统一工具分发，返回 {"ok": bool, ...}
        │
        ▼
MemoryStore
        │
        ├─► 文件层：AGENT.md / PROJECT.md
        ├─► SQLite：结构化元数据、session、process log、project context
        ├─► SQLite FTS5：memories_fts / chunks_fts，trigram tokenizer
        └─► VectorBackend：NullVectorBackend 或 Mem0VectorBackend
```

### 组件职责

| 组件 | 职责 |
| --- | --- |
| `MemoryStore` | 记忆系统核心 API。负责建库、迁移、文件写入、SQLite CRUD、检索融合、文档切块入库。 |
| `MemoryToolset` | LLM 工具适配层。发布工具 schema，把工具调用转发到 `MemoryStore`，并把异常包装为工具结果。 |
| `schema.sql` | SQLite 表结构。包含长期记忆、短期 session、过程日志、项目上下文、文档 chunk 和 FTS5 表。 |
| `retrieval.py` | BM25 检索和 Reciprocal Rank Fusion。中文查询会被拆成 trigram 窗口以提升命中率。 |
| `embedding.py` | 向量后端协议。默认 `NullVectorBackend` 不依赖外部服务；安装 mem0 后可走 `Mem0VectorBackend`。 |
| `doc_pipeline.py` | 文档读取与段落感知切块，目前支持 `.md` / `.txt` / `.json`。 |

### 存储边界

| 数据 | 主存储 | 检索方式 | 典型生命周期 |
| --- | --- | --- | --- |
| Agent 系统提示词 | `runtime/memory/agents/<agent_id>/AGENT.md` | 精确读取 | 长期稳定，由 agent owner 更新 |
| 项目稳定信息 | `runtime/memory/projects/<project_id>/PROJECT.md` | 精确读取 | 项目级稳定上下文，由主 agent 更新 |
| AgentSession | `agent_sessions` | `run_id` 精确读取，按 `project_id/agent_id/status` 列表查询 | 每次 agent run 一条 |
| 过程日志 | `process_events` | 按 `project_id/agent_id/run_id/event_type/query/since` 过滤 | 追加写，用于审计和复盘 |
| 项目成员职责 | `project_members` | 按 `project_id` 读取 | 从项目状态同步，也可工具 upsert |
| 项目产物索引 | `project_artifacts` | 按 `project_id/artifact_type` 读取 | 记录 Base、视图、表格、报告等引用 |
| Agent 经验 | `memories` + `memories_fts` + 可选向量集合 `agent_memories` | 混合检索，支持 `project_id/agent_id/run_id` 过滤 | 可反思、调权、淘汰 |
| 用户文档 chunk | `documents` / `doc_chunks` + `chunks_fts` + 可选向量集合 `user_documents` | 混合检索，支持 `corpus_id/project_id/agent_id/run_id/source_type` 过滤 | 用户上传后长期可查 |

### 写入路径

1. 点记忆写入：`profile_write` 或 `MemoryStore.write_agent_prompt()` /
   `write_project_profile()` 直接写 Markdown 文件。
2. 短期记忆写入：`session_start` 创建 `agent_sessions` 行，`session_finish`
   补齐输出、状态和结束时间。
3. 过程记忆写入：`process_log` 追加 `process_events`；`project_context_upsert`
   写 `PROJECT.md`、成员和产物。
4. 长期记忆写入：`memory_write` 写 `memories`，触发 FTS5 索引，同时向
   `VectorBackend.upsert()` 写入向量集合；相同 `content + agent_id +
   project_id + run_id + memory_type` 会被 SHA256 去重。
5. 文档写入：`doc_ingest` 读取文件、切块，写 `documents/doc_chunks`，触发
   FTS5 索引，并把每个 chunk 写入可选向量集合。

### 检索路径

1. `memory_search` / `doc_search` 先根据查询文本生成 FTS5 MATCH 表达式。
2. 同时调用向量后端；默认 `NullVectorBackend` 返回空列表，因此无需外部依赖。
3. BM25 排名和向量排名通过 Reciprocal Rank Fusion 融合。
4. 从 SQLite 重新读取命中项并应用元数据过滤。
5. 长期记忆会把融合分数乘以 `importance * confidence`，并更新
   `access_count/last_accessed`。

## 四层记忆

| 层级 | 存什么 | 存储形式 | 主要接口 |
| --- | --- | --- | --- |
| 点记忆 | Agent 系统提示词、项目稳定描述 | `runtime/memory/agents/<agent>/AGENT.md`、`runtime/memory/projects/<project>/PROJECT.md` | `profile_write` / `profile_read` |
| 短期记忆 | 每个 agent 每次 run 的输入、输出、scratchpad、metadata | `agent_sessions` | `session_start` / `session_finish` / `session_get` / `session_list` |
| 过程记忆 | 每次 run 的事件日志、项目成员职责、Base/视图/表格/报告等产物 | `process_events`、`project_members`、`project_artifacts` | `process_log` / `process_search` / `project_context_*` |
| 长期记忆 | 从项目中提取的经验、模式、流程知识、文档 chunk | `memories`、`documents`、`doc_chunks` + 可选向量后端 | `memory_*` / `doc_*` |

### Agent 自管理记忆的三种子类型

- **episodic（事件型）**：单次运行发生了什么。`BaseAgent.log_run` 在每次执行
  完成后会自动写入一条，无需 Agent 显式调用。
- **reflective（反思型）**：跨多次事件提炼出的模式与结论。由
  `memory_reflect` 工具生成并写入。
- **procedural（流程型）**：稳定的“怎么做”知识，比如格式约束、外部 API 的
  特殊用法。Agent 在发现稳定规律后主动写入。

### 用户上传文档的三种子类型

- **knowledge**：项目章程、规范、手册等参考资料。
- **instruction**：明确给 Agent 的偏好或约束（“关键风险用日语反馈给大阪团队”）。
- **domain_data**：结构化领域上下文，例如组织架构、干系人列表。

## 检索通道

```
query
  ├─► BM25 over FTS5（trigram 分词，CJK 友好）
  └─► Vector search（mem0 / Qdrant 等）—— 可选
          │
          ▼
    Reciprocal Rank Fusion（k=60）
          │
          ▼
    SQLite metadata 过滤（project_id / agent_id / run_id / since / source_type ...）
          │
          ▼
    返回 top_k 命中
```

向量后端缺失时（未安装 mem0），系统自动退化为纯 BM25 + 元数据过滤；中文查询
通过 [`retrieval.py`](retrieval.py) 中的 trigram 滑窗策略处理：长 CJK token
会被展开成所有 3-字符窗口再 OR 拼接，避免 trigram tokenizer 的整短语匹配限制。

## 快速开始

### 直接通过 CLI

```bash
# 1. 初始化项目演示数据（state.json）
python3 run_demo.py init-demo

# 2. 跑一轮 Agent 周期 —— 6 个 Agent 各写入一条 episodic 记忆
python3 run_demo.py run-cycle

# 3. 查看刚刚沉淀的记忆
python3 run_demo.py memory-list --limit 10

# 4. 混合检索
python3 run_demo.py memory-search --query "高风险" --top-k 3

# 5. 反思：从近期 episodic 记忆中提炼并存为 reflective 记忆
python3 run_demo.py memory-reflect --topic "高风险项目" --agent risk-assessor

# 6. 查看短期记忆和过程记忆
python3 run_demo.py session-list --agent risk-assessor
python3 run_demo.py process-list --project proj-atlas

# 7. 查看项目上下文（PROJECT.md + 成员 + 产物 + 最近事件）
python3 run_demo.py project-context --project proj-atlas

# 8. 摄取用户文档（uploads/ 下放置 .md / .txt 文件）
python3 run_demo.py doc-ingest \
    --path proj_atlas_charter.md \
    --source-type knowledge \
    --corpus proj-atlas \
    --project proj-atlas

# 9. 在用户文档中检索
python3 run_demo.py doc-search \
    --query "合规审计的截止日期" \
    --corpus proj-atlas \
    --project proj-atlas
```

### 在 Python 代码中使用

```python
from pathlib import Path
from pmo_mvp.memory import MemoryStore, MemoryToolset

store = MemoryStore(db_path=Path("runtime/memory.db"))
tools = MemoryToolset(store)

# 写入一条 episodic 记忆
tools.dispatch("memory_write", {
    "content": "proj-phoenix 因后端联调延期导致里程碑滑动 3 天",
    "agent_id": "risk-assessor",
    "memory_type": "episodic",
    "project_id": "proj-phoenix",
    "run_id": "run-20260505-001",
    "importance": 1.2,
    "tags": ["dependency", "milestone_slip"],
})

# 检索
result = tools.dispatch("memory_search", {
    "query": "联调延期",
    "project_id": "proj-phoenix",
    "agent_id": "risk-assessor",
    "top_k": 5,
})
print(result["result"]["results"])

store.close()
```

### 在 Anthropic / OpenAI 工具调用中使用

```python
import anthropic
from pmo_mvp.memory import MemoryStore, MemoryToolset

store = MemoryStore(db_path="runtime/memory.db")
tools = MemoryToolset(store)

client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    tools=tools.schemas(),         # 自动暴露记忆工具的 JSON schema
    messages=[{"role": "user", "content": "总结 proj-phoenix 最近的风险趋势。"}],
)

# 处理 tool_use 块
for block in response.content:
    if block.type == "tool_use":
        result = tools.dispatch(block.name, block.input)
        # ...回填给下一轮 messages
```

## 工具索引

`MemoryToolset.schemas()` 返回所有工具的 JSON Schema。`dispatch(name, args)`
统一返回 `{"ok": true, "result": ...}`；参数错误、存储错误会返回
`{"ok": false, "error": "ErrorType: message"}`，未知工具返回 `{"error": ...}`。

### 长期记忆工具

| 工具 | 用途 | 必填参数 | 重要可选参数 | 返回 |
| --- | --- | --- | --- | --- |
| `memory_write` | 写入 Agent 自管理长期记忆 | `content`, `memory_type`, `agent_id` | `project_id`, `run_id`, `tags`, `importance`, `confidence`, `expires_at`, `metadata` | `MemoryRecord` |
| `memory_search` | 混合检索长期记忆 | `query` | `top_k`, `memory_type`, `project_id`, `agent_id`, `run_id`, `since` | `{count, results}` |
| `memory_get` | 按 ID 精确读取长期记忆 | `memory_id` | 无 | `MemoryRecord` 或 `{error: "not_found"}` |
| `memory_reflect` | 从近期 episodic 记忆提炼 reflective 记忆 | `topic`, `agent_id` | `project_id`, `lookback` | `{reflection, source_count}` |
| `memory_weight_update` | 调整长期记忆权重、置信度、过期时间或元数据 | `memory_id` | `importance`, `confidence`, `expires_at`, `metadata` | 更新后的 `MemoryRecord` |
| `memory_evict` | 淘汰过期、低权重或超容量记忆 | 无 | `project_id`, `agent_id`, `run_id`, `memory_type`, `now`, `min_importance`, `max_records` | `{count, deleted_ids}` |

`memory_type` 取值为：

- `episodic`：单次 run 事件或结果。
- `reflective`：多次事件反思出的模式。
- `procedural`：稳定流程知识或操作规则。

权重字段用于长期记忆排序和淘汰：

- `importance`：重要性，默认 `1.0`，越高越优先召回和保留。
- `confidence`：可信度，默认 `1.0`，越高越优先召回。
- `expires_at`：显式过期时间。`memory_evict(now=...)` 会删除过期项。
- `access_count` / `last_accessed`：检索或 `memory_get` 时自动更新。

### 点记忆工具

| 工具 | 用途 | 必填参数 | 重要可选参数 | 返回 |
| --- | --- | --- | --- | --- |
| `profile_write` | 写 `AGENT.md` 或 `PROJECT.md` | `profile_type`, `owner_id`, `content` | `overwrite` | `{kind, owner_id, path, content}` |
| `profile_read` | 读 `AGENT.md` 或 `PROJECT.md` | `profile_type`, `owner_id` | 无 | profile 文件内容或 `{error: "not_found"}` |

`profile_type="agent"` 时文件路径是
`runtime/memory/agents/<owner_id>/AGENT.md`；`profile_type="project"` 时路径是
`runtime/memory/projects/<owner_id>/PROJECT.md`。`owner_id` 会做安全路径归一化。

### 短期记忆工具

| 工具 | 用途 | 必填参数 | 重要可选参数 | 返回 |
| --- | --- | --- | --- | --- |
| `session_start` | 创建一次 agent run 的 `AgentSession` | `agent_id` | `run_id`, `project_id`, `input_summary`, `scratchpad`, `metadata` | `AgentSession` |
| `session_finish` | 结束一次 `AgentSession` | `run_id` | `status`, `output_summary`, `scratchpad`, `metadata` | 更新后的 `AgentSession` |
| `session_get` | 按 `run_id` 精确读取 session | `run_id` | 无 | `AgentSession` 或 `{error: "not_found"}` |
| `session_list` | 列出近期 session | 无 | `project_id`, `agent_id`, `status`, `limit` | `{count, results}` |

`status` 取值为 `running`、`completed`、`failed`、`cancelled`。完成态会自动写
`ended_at`。

### 过程记忆工具

| 工具 | 用途 | 必填参数 | 重要可选参数 | 返回 |
| --- | --- | --- | --- | --- |
| `process_log` | 追加一条可审计过程事件 | `event_type`, `message` | `project_id`, `agent_id`, `run_id`, `payload` | `ProcessEvent` |
| `process_search` | 查询过程事件 | 无 | `project_id`, `agent_id`, `run_id`, `event_type`, `query`, `since`, `limit` | `{count, results}` |

过程事件适合记录“开始/结束 run、调用外部系统、写入表格、生成报告、风险升级”等
过程事实。它不参与向量检索，主要用于项目审计、复盘和调试。

### 项目上下文工具

| 工具 | 用途 | 必填参数 | 重要可选参数 | 返回 |
| --- | --- | --- | --- | --- |
| `project_context_upsert` | 写项目 profile、成员职责、项目产物引用 | `project_id` | `profile_content`, `members`, `artifacts` | 写入后的 profile/members/artifacts |
| `project_context_get` | 读取项目完整上下文 | `project_id` | 无 | `{project_id, profile, members, artifacts, recent_events}` |

`members` 的元素会传给 `upsert_project_member()`，典型字段：

```json
{
  "name": "赵敏",
  "role": "project_owner",
  "member_id": "user-open-id",
  "responsibility": "负责 Phoenix 项目推进",
  "metadata": {"department": "PMO"}
}
```

`artifacts` 的元素会传给 `upsert_project_artifact()`，典型字段：

```json
{
  "artifact_type": "base",
  "name": "项目台账",
  "external_id": "base-token",
  "url": "https://...",
  "metadata": {"table": "风险表"}
}
```

### 文档工具

| 工具 | 用途 | 必填参数 | 重要可选参数 | 返回 |
| --- | --- | --- | --- | --- |
| `doc_ingest` | 摄取用户文件，切块并建索引 | `file_path`, `source_type`, `corpus_id` | `project_id`, `agent_id`, `run_id`, `uploaded_by`, `metadata` | 文档 ID、chunk 数等摘要 |
| `doc_search` | 检索文档 chunk | `query` | `top_k`, `corpus_id`, `project_id`, `agent_id`, `run_id`, `source_type` | `{count, results}` |

`source_type` 取值为 `knowledge`、`instruction`、`domain_data`。搜索时可用
`source_type="all"` 表示不过滤类型。

### 工具调用示例

```python
from pathlib import Path
from pmo_mvp.memory import MemoryStore, MemoryToolset

store = MemoryStore(db_path=Path("runtime/memory.db"))
tools = MemoryToolset(store)

write = tools.dispatch("memory_write", {
    "content": "Phoenix 联调延期时需要优先核查后端接口 owner 和阻塞原因。",
    "memory_type": "procedural",
    "agent_id": "risk-assessor",
    "project_id": "proj-phoenix",
    "importance": 1.4,
    "confidence": 0.9,
    "tags": ["risk", "dependency"],
})

search = tools.dispatch("memory_search", {
    "query": "联调延期怎么处理",
    "agent_id": "risk-assessor",
    "project_id": "proj-phoenix",
    "memory_type": "all",
    "top_k": 5,
})

store.close()
```

## AgentSession 使用方法

`AgentSession` 是短期记忆，服务于“某个 agent 的某一次 run”。它保存这次 run 的
输入摘要、输出摘要、scratchpad、状态和元数据，主要用于：

- run 内上下文追踪：知道这次 agent 收到了什么、做了什么、产出了什么。
- 过程审计：用 `run_id` 串起 session、process events、episodic memory。
- 失败恢复：`status="failed"` 时保留输入、scratchpad 和错误元数据，便于重试。
- 长期记忆沉淀：run 结束后可把关键结果写入 `episodic` 或 `procedural`。

### 数据字段

| 字段 | 含义 |
| --- | --- |
| `run_id` | 单次运行 ID，主键。可调用方传入，也可由 `start_session()` 自动生成。 |
| `agent_id` | 执行 run 的 agent，例如 `risk-assessor`。 |
| `project_id` | 可选项目作用域。项目级 agent run 应尽量传入。 |
| `status` | `running`、`completed`、`failed`、`cancelled`。 |
| `input_summary` | 本次 run 的输入摘要，避免把大上下文全部塞入 session。 |
| `output_summary` | 本次 run 的结果摘要。 |
| `scratchpad` | 短期工作区，可保存中间判断、临时状态、错误上下文。 |
| `metadata` | 结构化扩展字段，例如模型名、token 用量、外部任务 ID、错误类型。 |
| `started_at` / `ended_at` | SQLite 自动生成的开始/结束时间。完成态会设置 `ended_at`。 |

### 推荐生命周期

```text
run begins
  ├─► session_start / start_session()
  ├─► process_log(event_type="agent_step", ...)
  ├─► agent executes
  ├─► process_log(event_type="artifact_written", ...)
  ├─► memory_write(memory_type="episodic", run_id=...)
  └─► session_finish(status="completed" / "failed" / "cancelled")
```

每个 run 应尽量共享同一个 `run_id`。同一个 `run_id` 会出现在：

- `agent_sessions.run_id`
- `process_events.run_id`
- `memories.run_id`
- `documents.run_id` / `doc_chunks.run_id`

这样可以从任何一个产物回溯到完整执行链路。

### 自动使用：现有 Python Agent

当前 `BaseAgent.log_run(state, summary, ctx)` 已经自动调用
`MemoryStore.record_agent_run()`。只要 `PMOCycleEngine` 传入 `memory`，每个 agent
结束时都会写入：

- 一条 `AgentSession`
- 两条 `process_events`：`session_started` / `session_finished`
- 一条 `episodic` 长期记忆，带同一个 `run_id`

```python
from pathlib import Path
from pmo_mvp.engine import PMOCycleEngine
from pmo_mvp.memory import MemoryStore
from pmo_mvp.store import JsonStateStore

memory = MemoryStore(db_path=Path("runtime/memory.db"))
engine = PMOCycleEngine(
    store=JsonStateStore(Path("runtime/state.json")),
    output_dir=Path("output"),
    memory=memory,
)

summary = engine.run()
memory.close()
```

如果某个 agent 的 `summary` 包含 `project_id`，session、过程日志和 episodic 记忆
都会带项目作用域；当前 demo 中部分 agent 是全局扫描，可能没有单一 `project_id`。

### 手动使用：直接调用 MemoryStore

适合纯 Python 编排或自定义 agent runtime。

```python
from pathlib import Path
from pmo_mvp.memory import MemoryStore

store = MemoryStore(db_path=Path("runtime/memory.db"))

session = store.start_session(
    agent_id="risk-assessor",
    project_id="proj-phoenix",
    input_summary="扫描 Phoenix 的任务、里程碑和会议纪要风险信号。",
    metadata={"trigger": "daily_cycle"},
)
run_id = session.run_id

store.record_process_event(
    project_id="proj-phoenix",
    agent_id="risk-assessor",
    run_id=run_id,
    event_type="risk_scan_started",
    message="开始扫描任务和会议纪要。",
)

# ...agent 执行...

store.write_memory(
    content="Phoenix 后端联调阻塞会放大月底里程碑风险。",
    agent_id="risk-assessor",
    project_id="proj-phoenix",
    run_id=run_id,
    memory_type="episodic",
    tags=["risk", "milestone"],
    importance=1.2,
)

store.finish_session(
    run_id=run_id,
    status="completed",
    output_summary="识别出 1 个中高风险，建议 48 小时内确认后端接口完成时间。",
    metadata={"risk_count": 1},
)

store.close()
```

### 手动使用：通过工具调用

适合 LLM agent。每一步都用 `MemoryToolset.dispatch()`，返回值可直接回填给模型。

```python
start = tools.dispatch("session_start", {
    "agent_id": "weekly-report-agent",
    "project_id": "proj-atlas",
    "input_summary": "生成 Atlas 项目本周周报。",
})
run_id = start["result"]["run_id"]

tools.dispatch("process_log", {
    "event_type": "report_rendered",
    "message": "Atlas 周报 Markdown 已生成。",
    "project_id": "proj-atlas",
    "agent_id": "weekly-report-agent",
    "run_id": run_id,
    "payload": {"path": "output/weekly_proj-atlas_2026-04-28.md"},
})

tools.dispatch("session_finish", {
    "run_id": run_id,
    "status": "completed",
    "output_summary": "完成 Atlas 周报，包含开放任务、风险和下周计划。",
})
```

注意：如果 `session_start` 不传 `run_id`，工具会自动生成。LLM runtime 需要从
`session_start` 的返回值里取出 `result.run_id`，再传给后续 `process_log`、
`memory_write`、`session_finish`。

### 失败与取消

失败时不要丢弃 session。应使用 `session_finish(status="failed")` 并把错误写入
`metadata` 或 `scratchpad`：

```python
store.finish_session(
    run_id=run_id,
    status="failed",
    output_summary="读取飞书 Base 失败，未生成风险扫描结果。",
    scratchpad="lark-cli returned permission denied while reading risk table.",
    metadata={"error_type": "permission_denied", "retryable": True},
)
```

取消时使用 `status="cancelled"`。如果只是更新运行中的 scratchpad，可以调用
`session_finish(status="running", scratchpad=...)`，这不会设置 `ended_at`。

### 查询 session

```python
# 精确读取一次 run
session = store.get_session(run_id)

# 查某个 agent 最近 20 次 run
sessions = store.list_sessions(agent_id="risk-assessor", limit=20)

# 查某项目失败的 run
failed = store.list_sessions(project_id="proj-phoenix", status="failed")

# 查同一 run 的过程日志
events = store.list_process_events(run_id=run_id, limit=100)

# 查同一 run 沉淀的长期记忆
memories = store.list_memories(run_id=run_id, memory_type="episodic")
```

CLI 对应命令：

```bash
python3 run_demo.py session-list --agent risk-assessor --limit 20
python3 run_demo.py session-list --project proj-phoenix --status failed
python3 run_demo.py process-list --run-id run-xxxx
python3 run_demo.py memory-list --run-id run-xxxx
```

## 与现有 Agent 的衔接

`BaseAgent.log_run(state, summary, ctx)` 在 `ctx.memory` 存在时会自动写入：

- 一条 `AgentSession` 短期记忆
- 两条过程事件（session started / finished）
- 一条 `episodic` 长期记忆

`PMOCycleEngine` 在每轮开始时会确保每个 agent 的 `AGENT.md` 存在，并把项目
状态同步到 `PROJECT.md`、`project_members`、`project_artifacts`。构造函数已支持
`memory` 参数：

```python
from pathlib import Path
from pmo_mvp.engine import PMOCycleEngine
from pmo_mvp.memory import MemoryStore
from pmo_mvp.store import JsonStateStore

memory = MemoryStore(db_path=Path("runtime/memory.db"))
engine = PMOCycleEngine(
    store=JsonStateStore(Path("runtime/state.json")),
    output_dir=Path("output"),
    memory=memory,    # 关键：传入即开启自动 episodic 落库
)
engine.run()
```

如果不传 `memory`，整套系统行为与原 MVP 完全一致——记忆是可选增强，不是硬依赖。

## 启用向量后端（可选）

```bash
pip install -e '.[memory]'           # 安装 mem0ai
export OPENAI_API_KEY=sk-...         # mem0 默认走 OpenAI 文本嵌入
```

之后无需任何代码改动，`build_backend()` 会优先尝试 `Mem0VectorBackend`，失败
（未安装 / 凭据缺失）时静默回落到 `NullVectorBackend`。检索路径在两种模式下
对调用方完全一致——只是融合时多 / 少了一路向量召回。

也可以显式注入自定义后端：

```python
from pmo_mvp.memory import MemoryStore, NullVectorBackend

store = MemoryStore(db_path="runtime/memory.db",
                    vector_backend=NullVectorBackend())  # 强制纯 BM25 模式
```

## 存储 Schema 概览

```sql
-- 详见 schema.sql
memories         (id, agent_id, run_id, project_id, memory_type, content, importance, confidence, access_count, expires_at, ...)
agent_sessions   (run_id, agent_id, project_id, status, input_summary, output_summary, scratchpad, metadata, ...)
process_events   (id, project_id, agent_id, run_id, event_type, message, payload, created_at)
project_members  (id, project_id, member_id, name, role, responsibility, metadata, ...)
project_artifacts(id, project_id, artifact_type, name, external_id, url, metadata, ...)
documents        (id, corpus_id, project_id, agent_id, run_id, filename, source_type, ...)
doc_chunks       (id, document_id, corpus_id, project_id, agent_id, run_id, source_type, chunk_index, content, ...)
memories_fts  -- FTS5 trigram，对应 memories.content
chunks_fts    -- FTS5 trigram，对应 doc_chunks.content
```

写入路径自带 SHA256 去重：相同内容 + 相同 `agent_id/project_id/run_id/memory_type`
只会落一条记录。长期记忆支持 `importance`、`confidence`、`access_count`、
`expires_at`，可用 `memory_weight_update` 调整权重，用 `memory_evict` 做过期、
低权重或容量上限淘汰。

## 运行测试

```bash
python3 -m unittest tests.test_memory -v
```

14 个测试覆盖：写入 / 去重 / 类型校验 / `project_id/agent_id/run_id` 过滤 /
点记忆文件 / AgentSession / 过程日志 / 项目上下文 / 权重与淘汰 / 文档摄取与检索 /
工具 dispatcher / 反思生成 / 端到端 Engine 集成。

## 路线图

下面这些点设计文档里写过、目前留作扩展点：

- **真正的 LLM 反思**：`MemoryToolset(reflect_fn=...)` 接受自定义函数，把
  默认的拼接式 stub 替换成真正的模型摘要调用。
- **Cross-encoder 重排**：在 RRF 融合后插入一层 reranker，对 top-50 重排出
  top-10。
- **uploads/ 文件监听**：用 `watchdog` 自动触发 `doc_ingest`，让用户拖文件
  即可入库。
- **更多文件类型**：当前 `doc_pipeline.read_text_file` 只接受 .md / .txt /
  .json，可扩展 PDF / DOCX 解析。
