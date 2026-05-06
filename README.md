# HiveMindAI Agent Runtime

这是一个基于飞书多维表格 Base 的多 Agent 虚拟项目办公室。当前仓库已经收敛到新的 `src/` 架构：`src/agent_runtime` 是唯一正式 agent loop，Feishu 能力和 Memory 能力都通过 `src/tool_integration` 注册为工具后再被 Agent 调用。

## 当前架构

```text
scripts/run_mvp_demo_chain.py
  -> agent_runtime.mvp.build_runtime_with_tool_integration()
     -> AgentRuntime
     -> ToolIntegrationExecutor
        -> scan src/feishu_adapter      # Feishu tools
        -> scan tool_integrations       # memory_tools + trace_tool
     -> CompositeTraceSink
        -> traces/*.jsonl
        -> runtime/memory.db
```

核心目录：

| 路径 | 作用 |
| --- | --- |
| `src/agent_runtime/` | 正式 agent loop、session、事件、配置、质量门和 MVP handlers |
| `src/agent_runtime/context.py` | `AgentContext`：运行时上下文窗口、scratchpad、memory 检索入口和 compact summary |
| `src/agent_runtime/memory/` | SQLite/FTS memory store、session checkpoint、process log、document chunks、memory toolset |
| `src/tool_integration/` | 工具注册、schema 校验、执行、事件与 job runtime |
| `src/feishu_adapter/` | 飞书 API 工具封装，注册 `feishu_*` 工具 |
| `tool_integrations/` | 仓库级工具，当前包含 `memory_tools.py` 和 `trace_tool.py` |
| `agents/*/` | 每个 Agent 的 `AGENT.md` 点记忆和 `agent.yaml` 权限配置 |
| `projects/enterprise_rag/` | 当前可运行项目的 `PROJECT.md`、项目状态和 Base 表 manifest |
| `scripts/run_mvp_demo_chain.py` | MVP 链路入口：秘书 -> 风险 -> 追问 -> 周报 -> 协调器 |

旧的 `pmo_mvp/` 本地 JSON demo 已移除。后续新能力不要再绕过 `src/agent_runtime` 和 `src/tool_integration`。

## 安装

请使用将要运行脚本的同一个 Python 解释器安装依赖：

```bash
python -m pip install -r requirements.txt
# 或开发模式
python -m pip install -e .
```

## 配置

根目录 `.env` 会被运行脚本和工具执行器自动读取。最少需要：

```bash
FEISHU_APP_ID=...
FEISHU_APP_SECRET=...
DEEPSEEK_API_KEY=...
```

`projects/enterprise_rag/project_state.yaml` 和 `table_manifest.yaml` 可以直接填写 Base `app_token` / `table_id`，也可以使用 `${FEISHU_*}` 占位符并在 `.env` 中提供对应值。

LLM 默认走 DeepSeek 的 OpenAI-compatible 接口：

```bash
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com      # 可选，默认即此值
HIVEMIND_LLM_MODEL=deepseek-chat                # 可选，默认即此模型
```

Coordinator 的 `think` 阶段默认必须调用 LLM 决策下一步 Agent；如需临时降级为规则兜底，可设置：

```bash
HIVEMIND_COORDINATOR_LLM_OPTIONAL=1
```

Memory 默认写入：

```text
runtime/memory.db
```

可用环境变量覆盖：

```bash
HIVEMIND_MEMORY_DB_PATH=/absolute/path/to/memory.db
```

## 运行

执行测试：

```bash
python -m pytest tests/ -q
```

运行 MVP 链路：

```bash
python scripts/run_mvp_demo_chain.py --skip-coordinator-write
```

允许协调器写回 Base 时去掉 `--skip-coordinator-write`：

```bash
python scripts/run_mvp_demo_chain.py
```

缺少飞书环境变量时脚本会以退出码 `2` 结束并打印缺失项；不会用 mock 伪造主链路结果。

## Memory Tools

`tool_integrations/memory_tools.py` 会把 `src/agent_runtime/memory` 中的工具注册进正式工具体系，调用路径与 Feishu tools 一致：

```text
Agent handler
  -> ToolIntegrationExecutor.call_tool()
  -> ToolRuntime.invoke()
  -> MemoryToolset / MemoryStore
  -> session.steps 审计记录
```

已暴露的 memory tools：

- `memory_write`、`memory_search`、`memory_get`、`memory_reflect`
- `memory_weight_update`、`memory_evict`
- `profile_write`、`profile_read`
- `session_start`、`session_finish`、`session_get`、`session_list`
- `process_log`、`process_search`
- `project_context_upsert`、`project_context_get`
- `doc_ingest`、`doc_search`

详细说明见 [`src/agent_runtime/memory/README.md`](src/agent_runtime/memory/README.md)。

## 设计边界

- `agent_runtime.session.AgentSession` 是运行时热状态，不存在于 SQLite 中。
- `agent_runtime.context.AgentContext` 是上下文管理机制，负责上下文预算和 compact summary。
- `agent_runtime.memory.AgentSessionCheckpoint` 是 SQLite checkpoint，用于审计、恢复和跨 run 查询。
- 业务 Agent 不直接调用飞书 SDK、不直接访问 SQLite；统一走 `ToolIntegrationExecutor`。
- Feishu Base 是业务事实源；Memory 是运行上下文、过程日志和经验沉淀，不替代 Base。
