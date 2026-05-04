import pytest
from pydantic import ValidationError

from agent_runtime.agent_io import AgentOutputBase, ProjectRecordSnapshot, ProjectStateOutput, RiskCandidate
from agent_runtime.enums import AgentName, Priority, ProjectStatus, RiskLevel, RiskType, TriggerType
from agent_runtime.session import AgentSession


def test_agent_session_initializes() -> None:
    session = AgentSession(
        session_id="session-1",
        run_id="run-1",
        project_id="enterprise_rag",
        agent_name=AgentName.PROJECT_SECRETARY,
        trigger_type=TriggerType.MANUAL,
    )

    assert session.status == "created"
    assert session.steps == []
    assert session.model_dump()["run_id"] == "run-1"


def test_project_state_output_initializes() -> None:
    output = ProjectStateOutput(
        run_id="run-1",
        project_id="enterprise_rag",
        agent_name=AgentName.PROJECT_SECRETARY,
        summary="项目状态巡检完成",
        project=ProjectRecordSnapshot(
            project_record_id="rec-project",
            project_name="企业知识库 RAG 系统",
            status=ProjectStatus.IN_PROGRESS,
            priority=Priority.P1,
        ),
    )

    assert output.project.project_name == "企业知识库 RAG 系统"
    assert output.tasks == []
    assert output.model_dump(mode="json")["agent_name"] == "project_secretary"


def test_risk_candidate_confidence_must_not_exceed_one() -> None:
    with pytest.raises(ValidationError):
        RiskCandidate(
            risk_title="延期风险",
            risk_type=RiskType.DELAY,
            risk_level=RiskLevel.HIGH,
            confidence=1.1,
            trigger_reason="任务超过截止时间",
            idempotency_key="risk-enterprise-rag-delay",
        )


def test_agent_output_base_defaults_proposed_creates_to_empty_list() -> None:
    output = AgentOutputBase(
        run_id="run-1",
        project_id="enterprise_rag",
        agent_name=AgentName.FOLLOWUP,
        summary="无新增追问",
    )

    assert output.proposed_creates == []
    assert output.proposed_patches == []


def test_enum_serializes_to_string() -> None:
    session = AgentSession(
        session_id="session-1",
        run_id="run-1",
        project_id="enterprise_rag",
        agent_name=AgentName.COORDINATOR,
        trigger_type=TriggerType.REPLAY,
    )

    dumped = session.model_dump(mode="json")
    assert dumped["agent_name"] == "coordinator"
    assert dumped["trigger_type"] == "replay"
