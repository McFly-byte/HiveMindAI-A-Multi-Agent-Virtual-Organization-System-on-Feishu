from __future__ import annotations

from runtime.models import TodoItem, new_id, now_ts


class TodoStore:
    def __init__(self) -> None:
        self._todos: dict[str, TodoItem] = {}
        self._order: list[str] = []

    def create(self, payload: dict) -> TodoItem:
        todo = TodoItem(
            todo_id=new_id("todo"),
            title=payload["title"],
            status="pending",
            created_by=payload.get("created_by", {}),
            source=payload.get("source", {}),
            assigned_agent=payload["assigned_agent"],
            action_type=payload["action_type"],
            action_args=payload.get("action_args", {}),
            task_context=payload.get("task_context", {}),
            user_visible_summary=payload.get("user_visible_summary", payload["title"]),
        )
        self._todos[todo.todo_id] = todo
        self._order.append(todo.todo_id)
        return todo

    def list(self, status: str | None = None) -> list[TodoItem]:
        items = [self._todos[i] for i in self._order if i in self._todos]
        if status:
            items = [x for x in items if x.status == status]
        return items

    def pull_next(self) -> TodoItem | None:
        for item in self.list("pending"):
            return item
        return None

    def get_by_index(self, one_based_index: int) -> TodoItem | None:
        candidates = [x for x in self.list() if x.status in {"pending", "failed"}]
        if one_based_index <= 0 or one_based_index > len(candidates):
            return None
        return candidates[one_based_index - 1]

    def update_status(self, todo_id: str, status: str, *, result: dict | None = None, error: str | None = None) -> TodoItem:
        todo = self._todos[todo_id]
        todo.status = status  # type: ignore[assignment]
        todo.result = result if result is not None else todo.result
        todo.error = error
        todo.updated_at = now_ts()
        return todo

    def to_list(self) -> list[dict]:
        return [x.to_dict() for x in self.list()]
