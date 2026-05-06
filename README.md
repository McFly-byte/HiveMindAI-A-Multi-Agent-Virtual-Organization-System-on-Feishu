# PMO MVP

> 当前合并后的正式运行时在 `src/agent_runtime/`，MVP 链路入口是
> `scripts/run_mvp_demo_chain.py`。本 README 下方仍保留早期 `pmo_mvp/`
> 本地 JSON demo 说明；合并后的真实项目结构请优先阅读
> [`docs/开发指引.md`](docs/开发指引.md) 和
> [`docs/当前项目结构与记忆分层.md`](docs/当前项目结构与记忆分层.md)。

这是一个基于 [`虚拟项目办公室需求分析与Agent职责设计.md`](/Users/hyper/zenshot/zenshot/PMO/虚拟项目办公室需求分析与Agent职责设计.md) 落地的最小可运行项目。

目标不是一次性做完整比赛交付，而是先跑通一个真实的多 Agent 项目治理闭环：

- 项目秘书 Agent：巡检任务和里程碑缺口
- 风险识别 Agent：判断延期、依赖、需求变更风险
- 追问 Agent：为缺口和高风险事项生成追问
- 资源协调 Agent：提出资源与优先级调整建议
- 周报 Agent：生成项目周报 Markdown
- 复盘 Agent：为已完成项目生成复盘 Markdown

当前版本默认使用本地 JSON 仓储，所以可以直接跑通。后续要接飞书 Base 时，只需要把仓储层替换成真实 `lark-cli base` / SDK 调用。

## 目录结构

```text
PMO/
├── README.md
├── pyproject.toml
├── run_demo.py
├── runtime/
│   └── state.json
├── output/
│   ├── weekly_*.md
│   └── retro_*.md
└── pmo_mvp/
    ├── __init__.py
    ├── agents.py
    ├── demo_data.py
    ├── engine.py
    ├── lark_base_contract.py
    ├── store.py
    └── utils.py
```

## 运行方式

先初始化演示数据：

```bash
cd /Users/hyper/zenshot/zenshot/PMO
python3 run_demo.py init-demo
```

然后运行一轮完整的虚拟项目办公室：

```bash
python3 run_demo.py run-cycle
```

查看当前状态：

```bash
python3 run_demo.py show-state
```

## 运行结果

运行 `run-cycle` 后会产生：

- `runtime/state.json`
- `output/weekly_*.md`
- `output/retro_*.md`

这些文件分别表示：

- Base 业务数据的本地镜像
- 周报 Agent 输出的周报
- 复盘 Agent 输出的复盘报告

## 这个 MVP 覆盖的需求

已经覆盖的核心需求：

- FR-01 多项目基础数据沉淀与状态建模
- FR-02 任务巡检与数据缺口识别
- FR-03 风险识别与分级
- FR-04 自动追问与信息补全
- FR-05 风险处置与资源协调建议
- FR-06 周报、风险摘要与管理汇报生成
- FR-08 项目复盘与经验沉淀
- FR-09 可追溯与行为审计
- FR-10 持续运行与触发机制

## 和真实飞书 Base 集成时怎么迁移

当前仓储层使用的是本地 JSON。迁移到真实 Base 时：

1. 保留 `agents.py` 和 `engine.py`
2. 用真实 Base 的读写实现替换 `JsonStateStore`
3. 按 [`pmo_mvp/lark_base_contract.py`](/Users/hyper/zenshot/zenshot/PMO/pmo_mvp/lark_base_contract.py) 中的接口契约接入
4. 再把定时触发和消息触发接到飞书自动化/机器人

## 设计取舍

- 为什么先用本地 JSON：为了保证“最小可运行”
- 为什么仍保留 Base 契约文件：为了后续接比赛真环境
- 为什么用启发式而不是 LLM：为了先把协同闭环跑通
