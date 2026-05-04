from enum import StrEnum


class AgentName(StrEnum):
    COORDINATOR = "coordinator"
    PROJECT_SECRETARY = "project_secretary"
    RISK_ANALYSIS = "risk_analysis"
    FOLLOWUP = "followup"
    WEEKLY_REPORT = "weekly_report"


class AgentRole(StrEnum):
    ORCHESTRATOR = "orchestrator"
    PROJECT_DATA_SECRETARY = "project_data_secretary"
    RISK_ANALYST = "risk_analyst"
    INFO_FOLLOWUP_SPECIALIST = "info_followup_specialist"
    PROJECT_REPORTER = "project_reporter"


class TriggerType(StrEnum):
    MANUAL = "manual"
    SCHEDULED_SCAN = "scheduled_scan"
    RECORD_UPDATED = "record_updated"
    WEEKLY_REPORT = "weekly_report"
    FEEDBACK = "feedback"
    REPLAY = "replay"


class EventType(StrEnum):
    CHECK_PROJECT_STATE = "check_project_state"
    ANALYZE_RISK = "analyze_risk"
    GENERATE_FOLLOWUP = "generate_followup"
    GENERATE_WEEKLY_REPORT = "generate_weekly_report"
    RUN_FULL_DEMO_CHAIN = "run_full_demo_chain"
    RECOVER_FOLLOWUP_REPLY = "recover_followup_reply"


class AgentRunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    WAITING_LLM = "waiting_llm"
    VERIFYING = "verifying"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class AgentStepType(StrEnum):
    OBSERVE = "observe"
    THINK = "think"
    ACT = "act"
    VERIFY = "verify"
    LOG = "log"


class ToolPermission(StrEnum):
    READ_ONLY = "read_only"
    WRITE_WITH_GATE = "write_with_gate"
    WRITE_DIRECT_DENIED = "write_direct_denied"


class ModelProvider(StrEnum):
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    DOUBAO = "doubao"
    GLM = "glm"
    MOCK_DISABLED = "mock_disabled"


class BaseTableName(StrEnum):
    PROJECTS = "Projects"
    TASKS = "Tasks"
    MILESTONES = "Milestones"
    RISKS = "Risks"
    FOLLOWUPS = "FollowUps"
    WEEKLY_REPORTS = "WeeklyReports"
    AGENT_RUNS = "AgentRuns"


class ProjectStatus(StrEnum):
    NOT_STARTED = "未开始"
    IN_PROGRESS = "进行中"
    BLOCKED = "阻塞"
    DELAYED = "已延期"
    DONE = "已完成"


class TaskStatus(StrEnum):
    NOT_STARTED = "未开始"
    IN_PROGRESS = "进行中"
    BLOCKED = "阻塞"
    WAITING_ACCEPTANCE = "待验收"
    DONE = "已完成"


class MilestoneStatus(StrEnum):
    NOT_STARTED = "未开始"
    IN_PROGRESS = "进行中"
    DONE = "已完成"
    DELAYED = "已延期"


class Priority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class ProjectHealth(StrEnum):
    HEALTHY = "健康"
    ATTENTION = "关注"
    RISK = "风险"
    SEVERE_RISK = "严重风险"


class RiskLevel(StrEnum):
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"


class RiskType(StrEnum):
    DELAY = "延期风险"
    DEPENDENCY_BLOCK = "依赖阻塞风险"
    RESOURCE_CONFLICT = "资源冲突风险"
    REQUIREMENT_CHANGE = "需求变更风险"
    COMMUNICATION_DISTORTION = "沟通失真风险"
    DATA_MISSING = "数据缺失风险"
    QUALITY = "交付质量风险"


class RiskStatus(StrEnum):
    TO_CONFIRM = "待确认"
    FOLLOWING = "跟进中"
    ESCALATED = "已升级"
    CLOSED = "已关闭"


class FollowUpStatus(StrEnum):
    TO_SEND = "待发送"
    WAITING_REPLY = "待回复"
    REPLIED = "已回复"
    CLOSED = "已关闭"


class ReportSendStatus(StrEnum):
    DRAFT = "草稿"
    TO_SEND = "待发送"
    SENT = "已发送"


class EvidenceSourceType(StrEnum):
    BASE_RECORD = "base_record"
    AGENT_OUTPUT = "agent_output"
    USER_REPLY = "user_reply"
    RULE = "rule"
    MANUAL_INPUT = "manual_input"


class ActionType(StrEnum):
    CALL_AGENT = "call_agent"
    CREATE_RECORD = "create_record"
    UPDATE_RECORD = "update_record"
    CREATE_FOLLOWUP = "create_followup"
    CREATE_RISK = "create_risk"
    CREATE_WEEKLY_REPORT = "create_weekly_report"
    UPDATE_PROJECT_HEALTH = "update_project_health"
    NOOP = "noop"


class QualityGateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"


class ErrorType(StrEnum):
    NONE = "none"
    INVALID_INPUT = "invalid_input"
    SESSION_INIT_FAILED = "session_init_failed"
    FEISHU_AUTH_FAILED = "feishu_auth_failed"
    FEISHU_API_FAILED = "feishu_api_failed"
    TABLE_NOT_FOUND = "table_not_found"
    FIELD_NOT_FOUND = "field_not_found"
    RECORD_NOT_FOUND = "record_not_found"
    EMPTY_RECORDS = "empty_records"
    WRITEBACK_FAILED = "writeback_failed"
    LLM_FAILED = "llm_failed"
    LLM_JSON_INVALID = "llm_json_invalid"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    DUPLICATE_RISK = "duplicate_risk"
    DUPLICATE_FOLLOWUP = "duplicate_followup"
    REPORT_EMPTY = "report_empty"
    AGENT_TIMEOUT = "agent_timeout"
    CRON_DUPLICATED = "cron_duplicated"
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"
