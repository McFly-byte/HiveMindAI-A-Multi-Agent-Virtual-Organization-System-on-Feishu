from __future__ import annotations

"""
This file defines the integration contract for replacing the local JSON store
with real Feishu Base operations.

It is intentionally lightweight: the current MVP is locally runnable, while the
competition version can bind these methods to real `lark-cli base` or SDK calls.
"""


class BaseGatewayContract:
    def list_projects(self) -> list[dict]:
        raise NotImplementedError

    def list_tasks(self) -> list[dict]:
        raise NotImplementedError

    def list_milestones(self) -> list[dict]:
        raise NotImplementedError

    def list_risk_issues(self) -> list[dict]:
        raise NotImplementedError

    def list_meeting_notes(self) -> list[dict]:
        raise NotImplementedError

    def list_weekly_reports(self) -> list[dict]:
        raise NotImplementedError

    def upsert_task(self, record: dict) -> None:
        raise NotImplementedError

    def upsert_risk_issue(self, record: dict) -> None:
        raise NotImplementedError

    def upsert_weekly_report(self, record: dict) -> None:
        raise NotImplementedError

    def append_follow_up(self, record: dict) -> None:
        raise NotImplementedError

    def append_coordination_suggestion(self, record: dict) -> None:
        raise NotImplementedError

    def append_agent_run(self, record: dict) -> None:
        raise NotImplementedError

