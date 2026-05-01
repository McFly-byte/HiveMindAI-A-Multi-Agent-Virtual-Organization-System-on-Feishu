from tools.risk_rule_tool import RiskRuleTool


def test_blocked_task_without_reason_hits_rule() -> None:
    hits = RiskRuleTool().evaluate_task({"状态": "阻塞", "阻塞说明": ""})
    assert "阻塞任务缺少阻塞说明" in hits
