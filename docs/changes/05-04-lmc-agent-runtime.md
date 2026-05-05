# 背景

为聚焦飞书 AI 校园挑战赛 MVP，收敛旧工程骨架，优先打通可审计、可回放的 Agent Runtime 层。

# 改动内容

- 新增 `src/agent_runtime`：枚举、Base 引用、事件、配置、Session、QualityGate、Agent IO、Runtime skeleton。
- 新增五类 Agent 的 `AGENT.md` 与 `agent.yaml`。
- 新增项目 manifest、共享 prompt 规则和 runtime 单测。
- 清理旧 adaptor、gateway、tool、service、schema 骨架。

# 影响范围

影响运行时数据结构、配置加载、Agent 边界和测试入口；未接入真实飞书、LLM 或 Tool 实现。

# 测试情况

`python -m pytest tests/test_agent_runtime_schemas.py tests/test_agent_config_loading.py tests/test_quality_gate_models.py` 通过，14 passed。
