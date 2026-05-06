from __future__ import annotations

from typing import Any

from agent_runtime.enums import ActionType, BaseTableName, FollowUpStatus, ReportSendStatus, RiskLevel, RiskStatus, RiskType
from agent_runtime.project_state import TableManifest
from agent_runtime.quality_gate import QualityCheckItem, QualityGateRequest, QualityGateResult, QualityGateStatus


AGENT_RUNS_COMPAT_FIELDS = {"ID", "Agent名称", "触发时间", "操作描述", "输入来源", "输出结果", "执行状态"}


class SimpleRuleQualityGate:
    """Deterministic MVP gate for writeback payloads."""

    def __init__(
        self,
        writable_tables: set[BaseTableName],
        table_manifests: dict[BaseTableName, TableManifest] | None = None,
    ) -> None:
        self._writable = writable_tables
        self._tables = table_manifests or {}

    async def verify(self, request: QualityGateRequest) -> QualityGateResult:
        if request.action_type not in (
            ActionType.CREATE_RECORD,
            ActionType.CREATE_RISK,
            ActionType.CREATE_FOLLOWUP,
            ActionType.CREATE_WEEKLY_REPORT,
            ActionType.UPDATE_RECORD,
            ActionType.UPDATE_PROJECT_HEALTH,
        ):
            return QualityGateResult.passed_result(sanitized_payload=request.payload)

        checks: list[QualityCheckItem] = []

        if request.action_type in (
            ActionType.CREATE_RECORD,
            ActionType.CREATE_RISK,
            ActionType.CREATE_FOLLOWUP,
            ActionType.CREATE_WEEKLY_REPORT,
        ):
            if not request.proposed_creates:
                checks.append(_check("non_empty_creates", False, "no proposed_creates"))
            for pc in request.proposed_creates:
                ok = pc.table_name in self._writable
                checks.append(_check(f"table_allowed:{pc.table_name}", ok, None if ok else "table not in coordinator writable_tables"))
                if not pc.fields:
                    checks.append(_check(f"non_empty_fields:{pc.table_name}", False, "fields empty"))
                checks.extend(self._check_manifest_fields(pc.table_name, pc.fields, operation="create"))
                checks.extend(self._check_idempotency(pc.idempotency_key, pc.fields, pc.table_name))
                checks.extend(self._check_table_specific_create(pc.table_name, pc.fields, pc.evidence_refs))

        if request.proposed_patches:
            for patch in request.proposed_patches:
                ok = patch.table_name in self._writable
                checks.append(_check(f"table_allowed:{patch.table_name}", ok, None if ok else "table not in coordinator writable_tables"))
                checks.append(_check(f"patch_record_id:{patch.table_name}", bool(patch.record_id), "record_id is required"))
                checks.append(_check(f"patch_non_empty_fields:{patch.table_name}", bool(patch.fields), "fields empty"))
                checks.extend(self._check_manifest_fields(patch.table_name, patch.fields, operation="update"))
                checks.extend(self._check_idempotency(patch.idempotency_key, patch.fields, patch.table_name, required=False))

        if any(not c.passed for c in checks):
            return QualityGateResult(
                status=QualityGateStatus.FAILED,
                passed=False,
                checks=checks,
                blocked_reason="one or more quality checks failed",
            )

        return QualityGateResult(
            status=QualityGateStatus.PASSED,
            passed=True,
            checks=checks,
            sanitized_payload=request.payload,
        )

    def _check_manifest_fields(
        self,
        table_name: BaseTableName,
        fields: dict[str, Any],
        *,
        operation: str,
    ) -> list[QualityCheckItem]:
        if table_name == BaseTableName.AGENT_RUNS:
            checks: list[QualityCheckItem] = []
            for name in fields:
                checks.append(_check(f"field_known:{table_name}.{name}", name in AGENT_RUNS_COMPAT_FIELDS, "field not supported by current AgentRuns table"))
            if operation == "create":
                for name in ("ID", "Agent名称", "触发时间", "操作描述", "执行状态"):
                    checks.append(_check(f"required_field:{table_name}.{name}", _present(fields.get(name)), "required AgentRuns field missing"))
            return checks

        manifest = self._tables.get(table_name)
        if manifest is None or not manifest.fields:
            return []
        by_name = {field.field_name: field for field in manifest.fields}
        checks: list[QualityCheckItem] = []
        for name in fields:
            field = by_name.get(name)
            checks.append(_check(f"field_known:{table_name}.{name}", field is not None, "field not declared in table_manifest.yaml"))
            if field is not None:
                checks.append(_check(f"field_write_allowed:{table_name}.{name}", field.write_allowed, "field is not write_allowed in table_manifest.yaml"))
        if operation == "create":
            for field in manifest.fields:
                if field.required and field.write_allowed:
                    checks.append(
                        _check(
                            f"required_field:{table_name}.{field.field_name}",
                            _present(fields.get(field.field_name)),
                            "required write field missing",
                        )
                    )
        return checks

    def _check_idempotency(
        self,
        idempotency_key: str | None,
        fields: dict[str, Any],
        table_name: BaseTableName,
        *,
        required: bool = True,
    ) -> list[QualityCheckItem]:
        if not required:
            return []
        field_key = str(fields.get("幂等键") or "")
        ok = bool(idempotency_key or field_key)
        checks = [_check(f"idempotency_key:{table_name}", ok, "idempotency_key or 幂等键 is required")]
        if idempotency_key and field_key:
            checks.append(
                _check(
                    f"idempotency_key_consistent:{table_name}",
                    idempotency_key == field_key,
                    "RecordCreate.idempotency_key must match fields['幂等键']",
                )
            )
        return checks

    def _check_table_specific_create(
        self,
        table_name: BaseTableName,
        fields: dict[str, Any],
        evidence_refs: list[Any],
    ) -> list[QualityCheckItem]:
        if table_name == BaseTableName.RISKS:
            return _check_risk_fields(fields, evidence_refs)
        if table_name == BaseTableName.FOLLOWUPS:
            return _check_followup_fields(fields)
        if table_name == BaseTableName.WEEKLY_REPORTS:
            return _check_weekly_report_fields(fields)
        return []


def _check(name: str, passed: bool, reason: str | None = None, *, severity: str = "error") -> QualityCheckItem:
    return QualityCheckItem(check_name=name, passed=passed, reason=None if passed else reason, severity=severity)


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _enum_value(value: Any, enum_cls: type) -> bool:
    try:
        enum_cls(value)
        return True
    except Exception:
        return False


def _check_risk_fields(fields: dict[str, Any], evidence_refs: list[Any]) -> list[QualityCheckItem]:
    checks = [
        _check("risk_title_non_empty", _present(fields.get("风险标题")), "风险标题不能为空"),
        _check("risk_type_enum", _enum_value(fields.get("风险类型"), RiskType), "风险类型不在枚举中"),
        _check("risk_level_enum", _enum_value(fields.get("风险等级"), RiskLevel), "风险等级不在枚举中"),
        _check("risk_status_enum", _enum_value(fields.get("当前状态"), RiskStatus), "当前状态不在枚举中"),
        _check("risk_trigger_reason_non_empty", _present(fields.get("触发原因")), "触发原因不能为空"),
        _check("risk_evidence_non_empty", bool(evidence_refs) or _present(fields.get("证据来源")), "风险必须包含 evidence_refs 或证据来源"),
    ]
    is_high = fields.get("风险等级") == RiskLevel.HIGH.value
    if is_high:
        checks.append(_check("high_risk_action_non_empty", _present(fields.get("建议动作")), "高风险必须有建议动作"))
        checks.append(
            _check(
                "high_risk_owner_or_escalation_non_empty",
                _present(fields.get("责任人")) or _present(fields.get("升级对象")),
                "高风险必须有责任人或升级对象",
            )
        )
    return checks


def _check_followup_fields(fields: dict[str, Any]) -> list[QualityCheckItem]:
    return [
        _check("followup_title_non_empty", _present(fields.get("追问标题")), "追问标题不能为空"),
        _check("followup_target_non_empty", _present(fields.get("追问对象")) or _present(fields.get("追问角色")), "追问对象或追问角色至少一个非空"),
        _check("followup_reason_non_empty", _present(fields.get("追问原因")), "追问原因不能为空"),
        _check("followup_message_non_empty", _present(fields.get("追问内容")), "追问内容不能为空"),
        _check("followup_related_non_empty", _present(fields.get("关联任务")) or _present(fields.get("关联风险")), "关联任务或关联风险至少一个非空"),
        _check("followup_status_enum", _enum_value(fields.get("追问状态"), FollowUpStatus), "追问状态不在枚举中"),
    ]


def _check_weekly_report_fields(fields: dict[str, Any]) -> list[QualityCheckItem]:
    checks = [
        _check("weekly_period_non_empty", _present(fields.get("周期")), "周期不能为空"),
        _check("weekly_project_summary_non_empty", _present(fields.get("项目摘要")), "项目摘要不能为空"),
        _check("weekly_progress_present", _present(fields.get("本周进展")), "本周进展不能为空"),
        _check("weekly_risk_summary_present", _present(fields.get("风险摘要")), "风险摘要不能为空"),
        _check("weekly_blockers_present", "阻塞项" in fields, "阻塞项字段必须保留结构"),
        _check("weekly_next_plan_present", _present(fields.get("下周计划")), "下周计划不能为空"),
        _check("weekly_decision_items_present", "待拍板事项" in fields or "决策建议" in fields, "决策建议或待拍板事项字段必须保留结构"),
        _check("weekly_send_status_enum", _enum_value(fields.get("发送状态"), ReportSendStatus), "发送状态不在枚举中"),
    ]
    return checks
