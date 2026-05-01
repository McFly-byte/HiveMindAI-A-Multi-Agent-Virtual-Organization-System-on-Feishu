# PMO Agent Office

PMO Agent Office 是参加“飞书 AI 校园挑战赛 / 飞书 AI 产品创新赛道”的 MVP 项目骨架。项目主题是 Multi-Agent Network：多维表格上的多智能体虚拟组织，业务场景是软件研发项目 PMO 的虚拟项目办公室。

## MVP 业务闭环

1. 人工或脚本在飞书 Base 中创建 Projects、Tasks、Milestones。
2. ProjectSecretaryAgent 读取 Base，识别任务超期、长期未更新、字段缺失、阻塞说明缺失、里程碑临期等异常。
3. ProjectSecretaryAgent 更新 Tasks 并创建 FollowUps。
4. RiskAnalysisAgent 基于规则和 LLM 解释创建 Risks，并更新 Projects 健康度。
5. FollowUpAgent 生成追问，读取人工回复后回写任务或风险状态。
6. WeeklyReportAgent 汇总项目进展、风险、阻塞和待拍板事项，写入 WeeklyReports。
7. 所有 Agent 的输入、输出、判断依据和执行状态写入 AgentRuns，并落本地 trace。

## 技术架构说明

- FastAPI 作为 Gateway，提供健康检查和手动触发入口。
- 轻量自研 Agent Runtime，统一 Observe / Think / Act / Verify / Log 流程。
- Agent 只做流程编排，不直接访问飞书 SDK、HTTP API 或 `.env`。
- Tool Layer 封装 Base 读写、LLM、Trace、AgentRuns 等外部能力。
- `adaptors/feishu` 封装飞书多维表格、文档、消息和字段映射。
- Pydantic schema 约束 Agent、Tool、Base 记录和 LLM JSON 输出。
- Trace 默认写入本地 `traces/YYYY-MM-DD/{run_id}.jsonl`，预留 LangSmith。

## 目录说明

- `app/`：FastAPI 应用入口、依赖和日志配置。
- `gateway/`：路由、鉴权、健康检查、Cron 预留和运行锁。
- `agents/`：四个固定 Agent 与基类。
- `agent_runtime/`：Session、Result、运行循环和校验器。
- `tools/`：所有外部能力和可复用动作封装。
- `adaptors/feishu/`：飞书 OpenAPI / SDK 适配层。
- `services/`：风险、追问、周报、Trace、Memory 等业务服务。
- `schemas/`：Base 表、AgentRun、LLM 输出等 Pydantic 模型。
- `prompts/`：要求 JSON 输出的 prompt 模板。
- `scripts/`：环境检查、Demo 数据导入和 Agent 手动触发脚本。
- `tests/`：最小 smoke test。

## 环境变量说明

复制 `.env.example` 为 `.env` 后填写：

- `API_KEY`：Gateway 关键 POST 接口的简单鉴权 key。
- `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_APP_TOKEN`：飞书应用与 Base 配置。
- `FEISHU_BASE_TABLE_CONFIG`：Base 表和字段白名单配置。
- `LLM_PROVIDER`、`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`：统一 LLMTool 预留配置。
- `LANGSMITH_TRACING`、`LANGSMITH_API_KEY`、`TRACE_LOCAL_DIR`：Trace 配置。

不要提交 `.env`，日志中也不要打印完整 token 或 secret。

## 本地启动命令

```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
curl http://localhost:8000/health
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
uvicorn app.main:app --reload
Invoke-RestMethod http://localhost:8000/health
```

## API 调用示例

```bash
curl -X POST http://localhost:8000/agents/project-secretary/run \
  -H "X-API-Key: local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{"project_id":"demo-rag","trigger_type":"手动"}'

curl -X POST http://localhost:8000/demo/run-full-chain \
  -H "X-API-Key: local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{"project_id":"demo-rag","trigger_type":"手动"}'
```

## 四个 Agent 的职责

- `ProjectSecretaryAgent`：项目巡检、异常标记、FollowUps 创建，不做最终风险定级。
- `RiskAnalysisAgent`：规则识别风险，LLM 负责解释、归因和建议，写 Risks 与 Projects 健康度。
- `FollowUpAgent`：生成具体追问，读取人工回复并回写 Tasks / Risks / FollowUps。
- `WeeklyReportAgent`：生成管理层可读周报，写 WeeklyReports，可后续扩展飞书文档。

## 飞书 Base 表说明

核心表为 Projects、Tasks、Milestones、Risks、FollowUps、WeeklyReports、AgentRuns。字段白名单和 table_id 占位配置位于 `config/base_tables.yaml`。当前 scaffold 不使用本地 mock 替代真实链路；飞书 API 未配置时，适配器会返回清晰错误或抛出未配置异常。

## Demo 链路

`POST /demo/run-full-chain` 目前按顺序触发四个 Agent 的占位运行结果，后续接入真实飞书 Base 后将串联项目巡检、风险识别、追问、人工回复回收和周报生成。

## 开发分支建议

不要直接在 `main` 开发。建议按职责拆分：`feat/lmc-scaffold-runtime`、`feat/xxx-feishu-adaptor`、`feat/xxx-risk-agents`、`feat/xxx-llm-demo`。提交前检查 `.env`、token 打印、目录边界和最小 smoke test。

## 当前 TODO

- 接入真实飞书多维表格 OpenAPI / SDK。
- 根据 `config/base_tables.yaml` 补齐真实 table_id 和字段类型。
- 完成风险规则、追问生成、周报生成的业务逻辑。
- 接入国内大模型 API，并对 JSON 输出做重试与修复。
- 完成 AgentRuns 的真实 Base 写入。
- 补充 Demo 数据导入脚本和端到端 smoke test。
