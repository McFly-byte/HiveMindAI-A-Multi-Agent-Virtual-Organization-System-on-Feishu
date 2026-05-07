from __future__ import annotations

from runtime.models import Event


class WebChannelAdapter:
    def message_to_event(self, *, text: str, user_id: str = "local_user", chat_id: str | None = None) -> Event:
        session_id = f"web:{chat_id}:{user_id}" if chat_id else f"web:{user_id}"
        return Event(
            event_type="channel.message.received",
            source="web_channel",
            target="frontdesk_agent",
            session_id=session_id,
            payload={"channel": "web", "user_id": user_id, "chat_id": chat_id, "text": text},
        )
