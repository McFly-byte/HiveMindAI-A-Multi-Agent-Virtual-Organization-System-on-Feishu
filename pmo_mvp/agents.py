from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from collections import defaultdict

from .utils import days_between, new_id


DONE_STATUSES = {"done", "closed", "completed"}
PRIORITY_WEIGHTS = {"high": 3, "medium": 2, "low": 1}


@dataclass
class AgentContext:
    today: date
    output_dir: Path
    memory: object | None = None  # Optional MemoryStore; episodic memory written when present
    run_id: str | None = None


class BaseAgent:
    agent_name = "base-agent"
    system_prompt = "你是虚拟项目办公室中的基础 Agent，负责按职责处理项目治理任务并留下可追溯记录。"

    def log_run(self, state: dict, summary: dict, ctx: AgentContext | None = None) -> str:
        run_id = new_id("run")
        episode = self._format_run_episode(summary)
        state["agent_runs"].append(
            {
                "id": run_id,
                "agent": self.agent_name,
                "ran_on": summary["ran_on"],
                "summary": summary,
            }
        )
        if ctx is not None and ctx.memory is not None:
            try:
                if hasattr(ctx.memory, "record_agent_run"):
                    ctx.memory.record_agent_run(
                        agent_id=self.agent_name,
                        summary=summary,
                        content=episode,
                        run_id=run_id,
                        project_id=summary.get("project_id"),
                    )
                else:
                    ctx.memory.write_memory(
                        content=episode,
                        agent_id=self.agent_name,
                        memory_type="episodic",
                        run_id=run_id,
                        project_id=summary.get("project_id"),
                        tags=[self.agent_name, "run_summary"],
                    )
            except Exception:
                # Memory is best-effort; agent execution must not fail because of it.
                pass
        return run_id

    def _format_run_episode(self, summary: dict) -> str:
        parts = [f"{self.agent_name} 于 {summary.get('ran_on')} 完成一次运行"]
        for key, value in summary.items():
            if key in {"ran_on", "project_id"}:
                continue
            parts.append(f"{key}={value}")
        return "；".join(parts)


class SecretaryAgent(BaseAgent):
    agent_name = "project-secretary"
    system_prompt = "你是项目秘书 Agent，负责巡检任务、里程碑和会议纪要中的信息缺口，并生成可追问的问题线索。"

    def run(self, state: dict, ctx: AgentContext) -> list[dict]:
        findings: list[dict] = []
        today = ctx.today.isoformat()
        milestone_map = {item["id"]: item for item in state["milestones"]}

        for task in state["tasks"]:
            if task["status"] not in DONE_STATUSES and days_between(task["last_updated"], today) >= 5:
                task["needs_follow_up"] = True
                findings.append(
                    {
                        "id": new_id("gap"),
                        "project_id": task["project_id"],
                        "record_type": "task",
                        "record_id": task["id"],
                        "type": "stale_task",
                        "owner": task["owner"] or self._project_owner(state, task["project_id"]),
                        "description": f"任务 {task['name']} 已 {days_between(task['last_updated'], today)} 天未更新。",
                    }
                )

            missing_fields = [field for field in ("owner", "status", "due_date") if not task.get(field)]
            if missing_fields:
                task["needs_follow_up"] = True
                findings.append(
                    {
                        "id": new_id("gap"),
                        "project_id": task["project_id"],
                        "record_type": "task",
                        "record_id": task["id"],
                        "type": "missing_fields",
                        "owner": self._project_owner(state, task["project_id"]),
                        "description": f"任务 {task['name']} 缺少字段: {', '.join(missing_fields)}。",
                    }
                )

            milestone = milestone_map.get(task["milestone_id"])
            if milestone and task["status"] not in DONE_STATUSES:
                if days_between(today, milestone["planned_date"]) <= 2:
                    findings.append(
                        {
                            "id": new_id("gap"),
                            "project_id": task["project_id"],
                            "record_type": "milestone",
                            "record_id": milestone["id"],
                            "type": "milestone_at_risk",
                            "owner": milestone["owner"],
                            "description": f"里程碑 {milestone['name']} 临近，但任务 {task['name']} 尚未完成。",
                        }
                    )

        for note in state["meeting_notes"]:
            if note["risk_signals"] and not note["synced_to_risks"]:
                findings.append(
                    {
                        "id": new_id("gap"),
                        "project_id": note["project_id"],
                        "record_type": "meeting_note",
                        "record_id": note["id"],
                        "type": "unsynced_meeting_risk",
                        "owner": self._project_owner(state, note["project_id"]),
                        "description": f"会议纪要 {note['title']} 存在风险信号但尚未同步到风险表。",
                    }
                )

        self.log_run(
            state,
            {
                "ran_on": today,
                "finding_count": len(findings),
                "types": sorted({item["type"] for item in findings}),
            },
            ctx,
        )
        return findings

    @staticmethod
    def _project_owner(state: dict, project_id: str) -> str:
        for project in state["projects"]:
            if project["id"] == project_id:
                return project["owner"]
        return "未知负责人"


class RiskAssessmentAgent(BaseAgent):
    agent_name = "risk-assessor"
    system_prompt = "你是风险识别 Agent，负责把任务缺口、依赖阻塞和会议风险信号转化为项目风险、等级和建议动作。"

    def run(self, state: dict, ctx: AgentContext, findings: list[dict]) -> list[dict]:
        today = ctx.today.isoformat()
        findings_by_project: dict[str, list[dict]] = defaultdict(list)
        for finding in findings:
            findings_by_project[finding["project_id"]].append(finding)

        created_risks: list[dict] = []
        for project in state["projects"]:
            if project["status"] != "active":
                continue

            project_findings = findings_by_project.get(project["id"], [])
            blocked_tasks = [
                task for task in state["tasks"]
                if task["project_id"] == project["id"] and task["blocked_reason"]
            ]
            signal_texts = [item["description"] for item in project_findings]
            signal_texts.extend(
                f"任务 {task['name']} 存在阻塞: {task['blocked_reason']}" for task in blocked_tasks
            )

            if not signal_texts:
                continue

            level = self._risk_level(project_findings, blocked_tasks)
            reason = "；".join(signal_texts[:4])
            suggested_action = self._suggested_action(level)

            existing = self._find_risk_by_day(state, project["id"], today)
            if existing:
                existing.update(
                    {
                        "level": level,
                        "reason": reason,
                        "suggested_action": suggested_action,
                        "should_escalate": level == "high",
                    }
                )
                risk_record = existing
            else:
                risk_record = {
                    "id": new_id("risk"),
                    "project_id": project["id"],
                    "title": f"{project['name']} 项目风险扫描",
                    "type": "project_health",
                    "level": level,
                    "reason": reason,
                    "owner": project["owner"],
                    "status": "open",
                    "suggested_action": suggested_action,
                    "should_escalate": level == "high",
                    "created_on": today,
                }
                state["risk_issues"].append(risk_record)

            project["risk_level"] = level
            project["health"] = "red" if level == "high" else "yellow"
            created_risks.append(risk_record)

        self.log_run(
            state,
            {
                "ran_on": today,
                "risk_count": len(created_risks),
                "high_risk_count": sum(1 for item in created_risks if item["level"] == "high"),
            },
            ctx,
        )
        return created_risks

    @staticmethod
    def _risk_level(findings: list[dict], blocked_tasks: list[dict]) -> str:
        score = len(findings) + len(blocked_tasks)
        if any(item["type"] == "unsynced_meeting_risk" for item in findings):
            score += 1
        if score >= 4:
            return "high"
        if score >= 2:
            return "medium"
        return "low"

    @staticmethod
    def _suggested_action(level: str) -> str:
        if level == "high":
            return "立即补齐信息、召开专项同步会，并评估资源支援或里程碑调整。"
        if level == "medium":
            return "48 小时内补齐状态并确认关键路径任务是否可按时完成。"
        return "持续观察。"

    @staticmethod
    def _find_risk_by_day(state: dict, project_id: str, day: str) -> dict | None:
        for item in state["risk_issues"]:
            if item["project_id"] == project_id and item.get("created_on") == day and item["type"] == "project_health":
                return item
        return None


class FollowUpAgent(BaseAgent):
    agent_name = "follow-up-agent"
    system_prompt = "你是追问 Agent，负责把信息缺口和高风险事项转化为面向责任人的具体追问。"

    def run(self, state: dict, ctx: AgentContext, findings: list[dict], risks: list[dict]) -> list[dict]:
        today = ctx.today.isoformat()
        follow_ups: list[dict] = []

        for finding in findings:
            question = self._question_for_finding(finding)
            record = {
                "id": new_id("fu"),
                "project_id": finding["project_id"],
                "recipient": finding["owner"],
                "related_record_id": finding["record_id"],
                "source_type": finding["type"],
                "question": question,
                "status": "open",
                "sent_on": today,
            }
            state["follow_ups"].append(record)
            follow_ups.append(record)

        for risk in risks:
            if risk["level"] == "high":
                record = {
                    "id": new_id("fu"),
                    "project_id": risk["project_id"],
                    "recipient": risk["owner"],
                    "related_record_id": risk["id"],
                    "source_type": "high_risk_confirmation",
                    "question": "当前风险已被识别为高风险，请补充是否需要调整里程碑、调配资源或升级管理层决策。",
                    "status": "open",
                    "sent_on": today,
                }
                state["follow_ups"].append(record)
                follow_ups.append(record)

        self.log_run(
            state,
            {
                "ran_on": today,
                "follow_up_count": len(follow_ups),
            },
            ctx,
        )
        return follow_ups

    @staticmethod
    def _question_for_finding(finding: dict) -> str:
        if finding["type"] == "stale_task":
            return f"{finding['description']} 请补充当前完成度、阻塞点和预计完成时间。"
        if finding["type"] == "missing_fields":
            return f"{finding['description']} 请补齐缺失字段，以便系统继续判断风险。"
        if finding["type"] == "milestone_at_risk":
            return f"{finding['description']} 请确认是否需要调整里程碑或拆分交付节点。"
        return f"{finding['description']} 请确认是否应同步到风险表，并说明影响范围。"


class ResourceCoordinatorAgent(BaseAgent):
    agent_name = "resource-coordinator"
    system_prompt = "你是资源协调 Agent，负责识别高风险项目中的负载瓶颈，并提出资源和优先级调整建议。"

    def run(self, state: dict, ctx: AgentContext, risks: list[dict]) -> list[dict]:
        today = ctx.today.isoformat()
        load_by_owner: dict[str, int] = defaultdict(int)
        owner_projects: dict[str, set[str]] = defaultdict(set)
        for task in state["tasks"]:
            if task["status"] not in DONE_STATUSES:
                load_by_owner[task["owner"]] += PRIORITY_WEIGHTS.get(task["priority"], 1)
                owner_projects[task["owner"]].add(task["project_id"])

        suggestions: list[dict] = []
        for risk in risks:
            if risk["level"] != "high":
                continue
            project_id = risk["project_id"]
            project_tasks = [task for task in state["tasks"] if task["project_id"] == project_id and task["status"] not in DONE_STATUSES]
            heaviest_task = max(project_tasks, key=lambda item: PRIORITY_WEIGHTS.get(item["priority"], 1), default=None)
            overloaded_owner = None
            for owner, load in load_by_owner.items():
                if project_id in owner_projects[owner] and load >= 5:
                    overloaded_owner = owner
                    break
            suggestion_text = "建议召开 15 分钟专项同步，确认关键路径任务与里程碑拆分方案。"
            if overloaded_owner and heaviest_task:
                suggestion_text = (
                    f"建议为 {overloaded_owner} 释放部分负载，并让其他成员提前并行准备任务，"
                    f"尤其关注 {heaviest_task['name']}。"
                )
            record = {
                "id": new_id("coord"),
                "project_id": project_id,
                "risk_id": risk["id"],
                "suggestion": suggestion_text,
                "status": "proposed",
                "created_on": today,
            }
            state["coordination_suggestions"].append(record)
            suggestions.append(record)

        self.log_run(
            state,
            {
                "ran_on": today,
                "suggestion_count": len(suggestions),
            },
            ctx,
        )
        return suggestions


class WeeklyReportAgent(BaseAgent):
    agent_name = "weekly-report-agent"
    system_prompt = "你是周报 Agent，负责按项目生成结构化周报，覆盖进度、风险、追问和协调建议。"

    def run(self, state: dict, ctx: AgentContext) -> list[dict]:
        today = ctx.today.isoformat()
        reports: list[dict] = []
        project_map = {item["id"]: item for item in state["projects"]}

        for project in state["projects"]:
            if project["status"] != "active":
                continue

            tasks = [task for task in state["tasks"] if task["project_id"] == project["id"]]
            open_tasks = [task for task in tasks if task["status"] not in DONE_STATUSES]
            risks = [item for item in state["risk_issues"] if item["project_id"] == project["id"] and item["status"] == "open"]
            follow_ups = [item for item in state["follow_ups"] if item["project_id"] == project["id"] and item["status"] == "open"]
            suggestions = [item for item in state["coordination_suggestions"] if item["project_id"] == project["id"]]

            report_md = self._render_report(project, open_tasks, risks, follow_ups, suggestions, project_map, today)
            path = ctx.output_dir / f"weekly_{project['id']}_{today}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report_md, encoding="utf-8")

            existing = self._find_report_by_day(state, project["id"], today)
            if existing:
                existing.update(
                    {
                        "project_summary": f"开放任务 {len(open_tasks)} 个，高风险 {sum(1 for item in risks if item['level'] == 'high')} 个。",
                        "risk_summary": "；".join(item["reason"] for item in risks[:2]),
                        "blockers": "；".join(task["blocked_reason"] for task in open_tasks if task["blocked_reason"]) or "无",
                        "next_week_plan": "继续推进关键路径任务并关闭高风险项。",
                        "decision_requests": "；".join(item["suggestion"] for item in suggestions[:2]) or "暂无",
                        "document_path": str(path),
                        "send_status": "generated",
                    }
                )
                report_record = existing
            else:
                report_record = {
                    "id": new_id("report"),
                    "project_id": project["id"],
                    "period": today,
                    "project_summary": f"开放任务 {len(open_tasks)} 个，高风险 {sum(1 for item in risks if item['level'] == 'high')} 个。",
                    "risk_summary": "；".join(item["reason"] for item in risks[:2]),
                    "blockers": "；".join(task["blocked_reason"] for task in open_tasks if task["blocked_reason"]) or "无",
                    "next_week_plan": "继续推进关键路径任务并关闭高风险项。",
                    "decision_requests": "；".join(item["suggestion"] for item in suggestions[:2]) or "暂无",
                    "document_path": str(path),
                    "send_status": "generated",
                }
                state["weekly_reports"].append(report_record)

            project["latest_weekly_report"] = str(path)
            reports.append(report_record)

        self.log_run(
            state,
            {
                "ran_on": today,
                "report_count": len(reports),
            },
            ctx,
        )
        return reports

    @staticmethod
    def _find_report_by_day(state: dict, project_id: str, day: str) -> dict | None:
        for item in state["weekly_reports"]:
            if item["project_id"] == project_id and item["period"] == day:
                return item
        return None

    @staticmethod
    def _render_report(
        project: dict,
        open_tasks: list[dict],
        risks: list[dict],
        follow_ups: list[dict],
        suggestions: list[dict],
        project_map: dict[str, dict],
        today: str,
    ) -> str:
        lines = [
            f"# {project['name']} 周报",
            "",
            f"- 生成日期：{today}",
            f"- 项目负责人：{project['owner']}",
            f"- 项目状态：{project['status']}",
            f"- 当前风险等级：{project['risk_level']}",
            "",
            "## 项目摘要",
            f"- 当前开放任务数：{len(open_tasks)}",
            f"- 当前风险数：{len(risks)}",
            f"- 待跟进追问数：{len(follow_ups)}",
            "",
            "## 关键风险",
        ]
        if risks:
            for risk in risks:
                lines.append(f"- [{risk['level']}] {risk['title']}：{risk['reason']}")
        else:
            lines.append("- 本周暂无显著风险。")

        lines.extend(["", "## 待处理追问"])
        if follow_ups:
            for item in follow_ups:
                lines.append(f"- {item['recipient']}：{item['question']}")
        else:
            lines.append("- 无")

        lines.extend(["", "## 资源协调建议"])
        if suggestions:
            for item in suggestions:
                lines.append(f"- {item['suggestion']}")
        else:
            lines.append("- 暂无")

        lines.extend(["", "## 开放任务"])
        for task in open_tasks:
            lines.append(
                f"- {task['name']} | owner={task['owner']} | status={task['status']} | due={task['due_date']} | blocked={task['blocked_reason'] or '无'}"
            )

        lines.extend(
            [
                "",
                "## 管理层建议",
                "- 建议重点关注高风险项目的关键路径任务与资源瓶颈。",
                "- 若当日仍无法补齐关键进度，应考虑调整里程碑或升级决策。",
            ]
        )
        return "\n".join(lines) + "\n"


class RetrospectiveAgent(BaseAgent):
    agent_name = "retrospective-agent"
    system_prompt = "你是复盘 Agent，负责从已完成项目中提炼问题、风险回顾和可复用经验。"

    def run(self, state: dict, ctx: AgentContext) -> list[dict]:
        today = ctx.today.isoformat()
        outputs: list[dict] = []
        for project in state["projects"]:
            if project["status"] != "completed":
                continue
            if any(item["project_id"] == project["id"] for item in state["retrospectives"]):
                continue

            tasks = [task for task in state["tasks"] if task["project_id"] == project["id"]]
            risks = [risk for risk in state["risk_issues"] if risk["project_id"] == project["id"]]
            overtime_tasks = [
                task for task in tasks
                if task["actual_hours"] > task["estimated_hours"]
            ]
            retro_md = self._render_retro(project, tasks, risks, overtime_tasks, today)
            path = ctx.output_dir / f"retro_{project['id']}_{today}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(retro_md, encoding="utf-8")

            record = {
                "id": new_id("retro"),
                "project_id": project["id"],
                "generated_on": today,
                "summary": f"超预算任务 {len(overtime_tasks)} 个，历史风险 {len(risks)} 个。",
                "document_path": str(path),
            }
            state["retrospectives"].append(record)
            outputs.append(record)

        self.log_run(
            state,
            {
                "ran_on": today,
                "retrospective_count": len(outputs),
            },
            ctx,
        )
        return outputs

    @staticmethod
    def _render_retro(project: dict, tasks: list[dict], risks: list[dict], overtime_tasks: list[dict], today: str) -> str:
        lines = [
            f"# {project['name']} 复盘报告",
            "",
            f"- 生成日期：{today}",
            f"- 项目负责人：{project['owner']}",
            "",
            "## 复盘摘要",
            f"- 历史任务数：{len(tasks)}",
            f"- 超预算任务数：{len(overtime_tasks)}",
            f"- 历史风险数：{len(risks)}",
            "",
            "## 主要发现",
        ]
        if overtime_tasks:
            for task in overtime_tasks:
                lines.append(
                    f"- {task['name']} 实际工时 {task['actual_hours']}h，高于预估 {task['estimated_hours']}h。"
                )
        else:
            lines.append("- 未发现明显超预算任务。")

        lines.extend(["", "## 风险回顾"])
        if risks:
            for risk in risks:
                lines.append(f"- {risk['title']}：{risk['reason']}")
        else:
            lines.append("- 无历史风险记录。")

        lines.extend(
            [
                "",
                "## 改进建议",
                "- 提前冻结需求口径，减少关键路径上的返工。",
                "- 对高风险任务增加中间检查点，而不是只看最终里程碑。",
                "- 将会议纪要中的风险信号更快沉淀到风险表。",
            ]
        )
        return "\n".join(lines) + "\n"
