# 长期记忆模块（Long-Term Memory）

为 PMO 多 Agent 系统提供可被工具调用的长期记忆层。Agent 通过工具读写记忆，
而不是直接访问存储；用户上传的文档与 Agent 自己沉淀的经验被分别管理但共享
同一套混合检索通道。

## 设计目标

- **两类记忆并存**：Agent 自管理（运行经验、模式、流程知识）+ 用户上传
  （知识文档、显式指令、领域数据）。
- **工具化访问**：对 LLM 暴露 6 个工具（`memory_write` / `memory_search` /
  `memory_get` / `memory_reflect` / `doc_ingest` / `doc_search`），每个工具
  自带 JSON Schema，可直接喂给 Anthropic / OpenAI / mem0 等支持工具调用的运行时。
- **可选向量化**：默认使用 SQLite FTS5 + BM25 跑通，无需任何外部依赖；安装
  `mem0ai` 后自动切换到向量 + BM25 RRF 融合检索。
- **MVP 友好**：不引入服务进程，所有持久化都落在 `runtime/memory.db`。

## 模块结构

```text
pmo_mvp/memory/
├── __init__.py        # 公共导出：MemoryStore / MemoryToolset / VectorBackend
├── schema.sql         # SQLite DDL：memories / documents / doc_chunks + FTS5 trigram
├── store.py           # MemoryStore：写入、检索、文档摄取的核心实现
├── embedding.py       # VectorBackend 协议：NullBackend（默认）/ Mem0Backend（可选）
├── retrieval.py       # BM25 over FTS5 + RRF 融合 + CJK trigram 滑窗
├── doc_pipeline.py    # 段落感知切块器（默认 1600 chars，overlap 200）
└── tools.py           # MemoryToolset：6 个工具的 schema 与 dispatcher
```

## 记忆分类

| 维度       | Agent 自管理                          | 用户上传                          |
| ---------- | ------------------------------------- | --------------------------------- |
| 数据形态   | 短文本观察 / 决策 / 模式              | 文档（.md / .txt）切块            |
| 存储集合   | `memories` 表 + `agent_memories` 向量 | `doc_chunks` 表 + `user_documents` 向量 |
| 写入方式   | Agent 调用 `memory_write` 工具         | 用户调用 `doc_ingest` 工具         |
| 子类型     | `episodic` / `reflective` / `procedural` | `knowledge` / `instruction` / `domain_data` |
| 检索范围   | `memory_search` / `memory_reflect`     | `doc_search`                      |

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
    SQLite metadata 过滤（agent_id / project_id / since / source_type ...）
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

# 6. 摄取用户文档（uploads/ 下放置 .md / .txt 文件）
python3 run_demo.py doc-ingest \
    --path proj_atlas_charter.md \
    --source-type knowledge \
    --corpus proj-atlas

# 7. 在用户文档中检索
python3 run_demo.py doc-search \
    --query "合规审计的截止日期" \
    --corpus proj-atlas
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
    "tags": ["dependency", "milestone_slip"],
})

# 检索
result = tools.dispatch("memory_search", {
    "query": "联调延期",
    "project_id": "proj-phoenix",
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
    tools=tools.schemas(),         # 自动暴露 6 个工具的 JSON schema
    messages=[{"role": "user", "content": "总结 proj-phoenix 最近的风险趋势。"}],
)

# 处理 tool_use 块
for block in response.content:
    if block.type == "tool_use":
        result = tools.dispatch(block.name, block.input)
        # ...回填给下一轮 messages
```

## 工具索引

| 工具名 | 适用场景 | 关键参数 |
| --- | --- | --- |
| `memory_write` | Agent 写入观察 / 决策 / 模式 | `content`, `memory_type`, `agent_id`, `project_id?`, `run_id?`, `tags?` |
| `memory_search` | 运行开始时拉取相关历史 | `query`, `top_k?`, `memory_type?`, `agent_id?`, `project_id?`, `since?` |
| `memory_get` | 按 ID 精确取一条 | `memory_id` |
| `memory_reflect` | 周期性把多条 episodic 提炼成 reflective | `topic`, `agent_id`, `project_id?`, `lookback?` |
| `doc_ingest` | 用户上传文档入库 | `file_path`, `source_type`, `corpus_id`, `uploaded_by?`, `metadata?` |
| `doc_search` | 在文档语料中检索 | `query`, `top_k?`, `corpus_id?`, `source_type?` |

每次 `dispatch` 都返回 `{"ok": bool, "result"|"error": ...}`，方便直接作为
工具结果回填给 LLM。

## 与现有 Agent 的衔接

`BaseAgent.log_run(state, summary, ctx)` 在 `ctx.memory` 存在时会自动写入一条
`episodic` 记忆——Agent 自身不需要任何改动。`PMOCycleEngine` 的构造函数已支持
`memory` 参数：

```python
from pmo_mvp.engine import PMOCycleEngine
from pmo_mvp.memory import MemoryStore
from pmo_mvp.store import JsonStateStore

memory = MemoryStore(db_path="runtime/memory.db")
engine = PMOCycleEngine(
    store=JsonStateStore("runtime/state.json"),
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
memories      (id, agent_id, run_id, project_id, memory_type, content, hash, tags, created_at)
documents     (id, corpus_id, filename, source_type, chunk_count, uploaded_by, metadata, created_at)
doc_chunks    (id, document_id, corpus_id, source_type, chunk_index, content, hash, created_at)
memories_fts  -- FTS5 trigram，对应 memories.content
chunks_fts    -- FTS5 trigram，对应 doc_chunks.content
```

写入路径自带 SHA256 去重：相同内容 + 相同作用域只会落一条记录。

## 运行测试

```bash
python3 -m unittest tests.test_memory -v
```

11 个测试覆盖：写入 / 去重 / 类型校验 / 过滤检索 / 文档摄取与检索 / 工具
dispatcher / 反思生成 / 端到端 Engine 集成。

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
