from __future__ import annotations

from agent_runtime.enums import ActionType, BaseTableName
from agent_runtime.quality_gate import QualityCheckItem, QualityGateRequest, QualityGateResult, QualityGateStatus


class SimpleRuleQualityGate:
    """Deterministic MVP gate: block empty payloads and tables outside coordinator policy."""

    def __init__(self, writable_tables: set[BaseTableName]) -> None:
        self._writable = writable_tables

    async def verify(self, request: QualityGateRequest) -> QualityGateResult:
        if request.action_type not in (
            ActionType.CREATE_RECORD,
            ActionType.CREATE_RISK,
            ActionType.CREATE_FOLLOWUP,
            ActionType.CREATE_WEEKLY_REPORT,
        ):
            return QualityGateResult.passed_result(sanitized_payload=request.payload)

        checks: list[QualityCheckItem] = []

        if request.action_type in (ActionType.CREATE_RECORD, ActionType.CREATE_RISK, ActionType.CREATE_FOLLOWUP):
            if not request.proposed_creates:
                return QualityGateResult.failed_result(
                    "QualityGate: proposed_creates is empty",
                    checks=[
                        QualityCheckItem(
                            check_name="non_empty_creates",
                            passed=False,
                            reason="no proposed_creates",
                        )
                    ],
                )
            for pc in request.proposed_creates:
                ok = pc.table_name in self._writable
                checks.append(
                    QualityCheckItem(
                        check_name=f"table_allowed:{pc.table_name}",
                        passed=ok,
                        reason=None if ok else "table not in coordinator writable_tables",
                    )
                )
                if not pc.fields:
                    checks.append(
                        QualityCheckItem(
                            check_name=f"non_empty_fields:{pc.table_name}",
                            passed=False,
                            reason="fields empty",
                        )
                    )

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
