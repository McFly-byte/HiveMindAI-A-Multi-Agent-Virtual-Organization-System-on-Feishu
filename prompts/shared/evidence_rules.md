# Evidence Rules

- 所有风险、追问、周报结论必须有 evidence_refs。
- evidence_refs 应尽量追溯到 Base 表、record_id、field_name、value_snapshot。
- 如果证据不足，必须显式输出 need_more_evidence 或 blocked。
- 不得用常识替代业务证据。
