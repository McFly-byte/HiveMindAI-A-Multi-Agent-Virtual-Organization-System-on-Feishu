from agent_runtime.session import AgentSession


def validate_session_finished(session: AgentSession) -> None:
    """Validate minimal completion invariants for an Agent session."""
    if session.status not in {"success", "failed", "partial_success"}:
        raise ValueError(f"Invalid terminal status: {session.status}")
