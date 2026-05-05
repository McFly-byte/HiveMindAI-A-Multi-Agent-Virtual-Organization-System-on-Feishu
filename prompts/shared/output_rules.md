# Structured Output Rules

- 所有 Agent 必须输出 JSON。
- 输出必须匹配对应 Pydantic schema。
- 不允许输出 Markdown 包裹 JSON。
- 不允许在 JSON 外输出解释文字。
- 字段缺失时使用 null 或空数组，不得编造。
- 所有写回建议必须放入 proposed_creates 或 proposed_patches。
