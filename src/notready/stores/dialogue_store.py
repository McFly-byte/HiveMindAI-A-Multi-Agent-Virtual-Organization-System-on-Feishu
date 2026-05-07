from __future__ import annotations

from runtime.models import DialogueState, new_id, now_ts


class DialogueStore:
    def __init__(self) -> None:
        self._dialogues: dict[str, DialogueState] = {}

    def create(
        self,
        *,
        session_id: str,
        business_agent: str,
        intent: str,
        slots: dict,
        mode: str,
        user_goal: str,
        dialogue_summary: str = "",
        status: str = "collecting",
    ) -> DialogueState:
        dlg = DialogueState(
            dialogue_id=new_id("dlg"),
            session_id=session_id,
            status=status,  # type: ignore[arg-type]
            business_agent=business_agent,
            intent=intent,
            slots=dict(slots),
            mode=mode,
            user_goal=user_goal,
            dialogue_summary=dialogue_summary,
        )
        self._dialogues[dlg.dialogue_id] = dlg
        return dlg

    def get(self, dialogue_id: str | None) -> DialogueState | None:
        if not dialogue_id:
            return None
        return self._dialogues.get(dialogue_id)

    def update_slots(self, dialogue_id: str, updates: dict) -> DialogueState:
        dlg = self._dialogues[dialogue_id]
        for k, v in updates.items():
            if v is not None and v != "":
                dlg.slots[k] = v
        dlg.updated_at = now_ts()
        return dlg

    def set_status(self, dialogue_id: str, status: str) -> None:
        dlg = self._dialogues[dialogue_id]
        dlg.status = status  # type: ignore[assignment]
        dlg.updated_at = now_ts()
