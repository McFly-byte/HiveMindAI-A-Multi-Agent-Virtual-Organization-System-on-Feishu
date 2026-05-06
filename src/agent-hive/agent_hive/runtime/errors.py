class HiveRuntimeError(Exception):
    pass


class AgentExecutionError(HiveRuntimeError):
    pass


class ToolExecutionError(HiveRuntimeError):
    pass


class OrchestrationError(HiveRuntimeError):
    pass
