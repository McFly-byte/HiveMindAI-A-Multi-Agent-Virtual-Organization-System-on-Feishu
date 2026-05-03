from __future__ import annotations

from datetime import date
from pathlib import Path

from .agents import (
    AgentContext,
    FollowUpAgent,
    ResourceCoordinatorAgent,
    RetrospectiveAgent,
    RiskAssessmentAgent,
    SecretaryAgent,
    WeeklyReportAgent,
)


class PMOCycleEngine:
    def __init__(self, store, output_dir: Path) -> None:
        self.store = store
        self.output_dir = output_dir
        self.secretary = SecretaryAgent()
        self.risk = RiskAssessmentAgent()
        self.follow_up = FollowUpAgent()
        self.coordinator = ResourceCoordinatorAgent()
        self.reporter = WeeklyReportAgent()
        self.retrospective = RetrospectiveAgent()

    def run(self) -> dict:
        state = self.store.load()
        ctx = AgentContext(
            today=date.fromisoformat(state["meta"]["today"]),
            output_dir=self.output_dir,
        )

        findings = self.secretary.run(state, ctx)
        risks = self.risk.run(state, ctx, findings)
        follow_ups = self.follow_up.run(state, ctx, findings, risks)
        suggestions = self.coordinator.run(state, ctx, risks)
        reports = self.reporter.run(state, ctx)
        retros = self.retrospective.run(state, ctx)

        self.store.save(state)
        return {
            "today": state["meta"]["today"],
            "secretary_findings": len(findings),
            "risks_created_or_updated": len(risks),
            "follow_ups_created": len(follow_ups),
            "coordination_suggestions": len(suggestions),
            "weekly_reports_generated": len(reports),
            "retrospectives_generated": len(retros),
            "state_path": str(self.store.path),
            "output_dir": str(self.output_dir),
        }

