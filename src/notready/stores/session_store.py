from __future__ import annotations

from runtime.models import SessionState, now_ts


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def get_or_create(self, *, channel: str, user_id: str, chat_id: str | None = None) -> SessionState:
        session_id = self.make_session_id(channel=channel, user_id=user_id, chat_id=chat_id)
        existing = self._sessions.get(session_id)
        if existing:
            existing.updated_at = now_ts()
            return existing
        st = SessionState(session_id=session_id, channel=channel, user_id=user_id, chat_id=chat_id)
        self._sessions[session_id] = st
        return st

    @staticmethod
    def make_session_id(*, channel: str, user_id: str, chat_id: str | None = None) -> str:
        if channel == "cli" or not chat_id:
            return f"{channel}:{user_id}"
        return f"{channel}:{chat_id}:{user_id}"

    def get(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)

    def set_active_dialogue(self, session_id: str, dialogue_id: str | None) -> None:
        st = self._sessions[session_id]
        st.active_dialogue_id = dialogue_id
        st.updated_at = now_ts()

    def set_last_project(self, session_id: str, project_name: str | None) -> None:
        st = self._sessions[session_id]
        if project_name:
            st.last_project = project_name
        st.updated_at = now_ts()
