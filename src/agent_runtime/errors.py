class AgentRuntimeError(Exception):
    """Base exception for agent runtime errors."""


class AgentConfigError(AgentRuntimeError):
    """Raised when agent configuration is invalid."""


class AgentSessionError(AgentRuntimeError):
    """Raised when agent session cannot be created or updated."""


class AgentOutputValidationError(AgentRuntimeError):
    """Raised when agent output does not match expected schema."""


class QualityGateBlockedError(AgentRuntimeError):
    """Raised when QualityGate blocks a proposed write action."""


class AgentTimeoutError(AgentRuntimeError):
    """Raised when an agent run exceeds runtime timeout."""


class AgentNotFoundError(AgentRuntimeError):
    """Raised when an agent name cannot be resolved."""
