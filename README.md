# HiveMindAI Agent Hive

一个基于飞书的通用 multi-agent runtime。当前新 runtime 位于 `src/agent-hive/agent_hive`，Agent 通过 `agents/*/agent.yaml` 注册。

## 架构

```text
Feishu IM / Scheduled Event / stdin
  -> EventDaemon
  -> HiveRuntime
  -> Orchestrator
  -> Business Agents
  -> feishu_tool_agent
  -> src/feishu_adapter/*
```

核心边界：

- `orchestrator` 负责理解事件并分发给业务 agent。
- 业务 agent 可以直接使用 memory。
- 业务 agent 不能直接调用 Feishu tool。
- 所有 Feishu API 调用必须委托给 `feishu_tool_agent`。
- `feishu_tool_agent` 负责发现知识空间、Base、表、字段，并把发现结果写入 memory。

## 目录

| 路径 | 说明 |
| --- | --- |
| `src/agent-hive/agent_hive` | 新 agent runtime |
| `agents/*/agent.yaml` | Agent 注册配置 |
| `agents/*/AGENT.md` | Agent prompt |
| `src/feishu_adapter` | 现有 Feishu API adapter |
| `runtime/memory.db` | 默认 memory 数据库 |
| `tests/agent_hive` | 新 runtime 测试 |

## 环境变量

根目录 `.env` 会在启动时自动读取。

```bash
FEISHU_APP_ID=...
FEISHU_APP_SECRET=...
DEEPSEEK_API_KEY=...
```

如果使用其他 OpenAI-compatible LLM：

```bash
HIVEMIND_LLM_API_KEY=...
HIVEMIND_LLM_BASE_URL=...
```

## 安装

```bash
uv sync
```

## 常用命令

列出 Agent：

```bash
PYTHONPATH=src:src/agent-hive uv run python -m agent_hive.main list-agents
```

启动常驻 agent loop，监听飞书 IM：

```bash
PYTHONPATH=src:src/agent-hive uv run python -m agent_hive.main --debug serve \
  --project-id enterprise_rag \
  --feishu-im
```

启动飞书 IM + FR-02 定时巡检：

```bash
PYTHONPATH=src:src/agent-hive uv run python -m agent_hive.main --debug serve \
  --project-id enterprise_rag \
  --feishu-im \
  --fr02-inspection \
  --fr02-interval-seconds 3600
```

手动发送一次事件：

```bash
PYTHONPATH=src:src/agent-hive uv run python -m agent_hive.main run \
  --event-type fr02.inspection.requested \
  --project-id enterprise_rag \
  --target-agent orchestrator \
  --payload '{"summary":"FR-02巡检","inspection_type":"fr02_task_data_gap"}'
```

## FR-02 巡检

FR-02 用于发现：

- 任务表和里程碑表中缺失负责人、截止时间、进度说明的记录。
- 超期且长期未更新的任务或里程碑。
- 会议纪要中提到的问题、风险、待办是否未同步到任务/风险表。

默认目标：

- 知识空间：`项目中枢`
- Base：`enterprise_rag表`

表发现逻辑：

1. 先从 memory 复用已发现的资源。
2. 查飞书知识空间，找到 Base app_token。
3. 列出 Base 内所有表。
4. 优先按名称匹配。
5. 名称不匹配时读取字段 schema，自动识别任务表、里程碑表、会议纪要表、风险表。
6. 将 app_token、table_id、字段识别结果和巡检结果写入 memory。

## 测试

```bash
PYTHONPATH=src:src/agent-hive uv run pytest tests/agent_hive -q
```

如果本机 `uv` 缓存权限有问题，也可以在已安装依赖的环境中跑：

```bash
PYTHONPATH=src:src/agent-hive python -m pytest tests/agent_hive -q
```

## 调试

打开 debug 日志：

```bash
AGENT_HIVE_DEBUG=1
```

或使用 CLI 参数：

```bash
--debug
```

退出常驻进程：

```text
Ctrl+C
```
