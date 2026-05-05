from agent_runtime.base_refs import EvidenceRef, RecordCreate
from agent_runtime.enums import ActionType, BaseTableName, EvidenceSourceType, QualityGateStatus
from agent_runtime.quality_gate import QualityCheckItem, QualityGateRequest, QualityGateResult


def test_quality_gate_request_accepts_creates_and_evidence() -> None:
    evidence = EvidenceRef(
        evidence_id="ev-1",
        source_type=EvidenceSourceType.BASE_RECORD,
        summary="任务已延期",
        table_name=BaseTableName.TASKS,
        record_id="task-1",
    )
    create = RecordCreate(
        table_name=BaseTableName.RISKS,
        fields={"风险标题": "延期风险"},
        idempotency_key="risk-task-1-delay",
        evidence_refs=[evidence],
    )
    request = QualityGateRequest(
        run_id="run-1",
        project_id="enterprise_rag",
        action_type=ActionType.CREATE_RISK,
        payload={"source": "risk_analysis"},
        proposed_creates=[create],
        evidence_refs=[evidence],
        schema_name="RiskAnalysisOutput",
    )

    assert request.proposed_creates[0].table_name == BaseTableName.RISKS
    assert request.evidence_refs[0].record_id == "task-1"


def test_quality_gate_result_can_express_passed() -> None:
    result = QualityGateResult.passed_result(
        checks=[QualityCheckItem(check_name="schema", passed=True)]
    )

    assert result.passed is True
    assert result.status == QualityGateStatus.PASSED


def test_quality_gate_result_can_express_failed_with_next_action() -> None:
    result = QualityGateResult.failed_result(
        blocked_reason="缺少 evidence_refs",
        suggested_next_action="补充 Base 证据后重试",
    )

    assert result.passed is False
    assert result.status == QualityGateStatus.FAILED
    assert result.blocked_reason == "缺少 evidence_refs"
    assert result.suggested_next_action == "补充 Base 证据后重试"
