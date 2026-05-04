from __future__ import annotations

from collections import deque
from typing import Any
from urllib.parse import quote

from tooltest.tools import ToolRegistry, ToolSpec
from tooltest.events import EventBus

from tools.feishu_client import feishu_request


CONTACT_PREFIX = "/open-apis/contact/v3"

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

DEFAULT_MAX_DEPARTMENTS = 100
DEFAULT_MAX_USERS = 5000
DEFAULT_MAX_MATCHES = 20

HARD_MAX_DEPARTMENTS = 1000
HARD_MAX_USERS = 50000
HARD_MAX_MATCHES = 100


def _path(value: str) -> str:
    return quote(str(value), safe="")


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None and v != ""}


def _first_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _as_bool_str(value: Any) -> str | None:
    if value is None:
        return None
    return "true" if bool(value) else "false"


def _as_int(
    value: Any,
    default: int,
    *,
    min_value: int = 1,
    max_value: int | None = None,
) -> int:
    try:
        result = int(value)
    except Exception:
        result = default

    result = max(min_value, result)
    if max_value is not None:
        result = min(max_value, result)
    return result


def _page_size(args: dict[str, Any]) -> int:
    return _as_int(
        args.get("page_size", DEFAULT_PAGE_SIZE),
        DEFAULT_PAGE_SIZE,
        min_value=1,
        max_value=MAX_PAGE_SIZE,
    )


def _query(args: dict[str, Any], *keys: str) -> dict[str, Any]:
    return _clean({key: args.get(key) for key in keys})


def _items(data: dict[str, Any]) -> list[dict[str, Any]]:
    value = (
        data.get("items")
        or data.get("users")
        or data.get("user_list")
        or data.get("departments")
        or data.get("department_list")
        or []
    )
    return value if isinstance(value, list) else []


def _as_page(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "items": _items(data),
        "has_more": bool(data.get("has_more", False)),
        "page_token": data.get("page_token") or data.get("next_page_token") or "",
    }


def _compact_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": _first_str(user, "user_id"),
        "open_id": _first_str(user, "open_id"),
        "union_id": _first_str(user, "union_id"),
        "name": _first_str(user, "name"),
        "en_name": _first_str(user, "en_name"),
        "nickname": _first_str(user, "nickname"),
        "email": _first_str(user, "email"),
        "mobile": _first_str(user, "mobile"),
        "department_ids": user.get("department_ids") or [],
        "leader_user_id": _first_str(user, "leader_user_id"),
        "employee_no": _first_str(user, "employee_no"),
        "employee_type": user.get("employee_type"),
        "status": user.get("status") or {},
    }


def _compact_department(department: dict[str, Any]) -> dict[str, Any]:
    return {
        "department_id": _first_str(department, "department_id"),
        "open_department_id": _first_str(department, "open_department_id"),
        "name": _first_str(department, "name"),
        "parent_department_id": _first_str(department, "parent_department_id"),
        "leader_user_id": _first_str(department, "leader_user_id"),
        "chat_id": _first_str(department, "chat_id"),
        "member_count": department.get("member_count"),
        "status": department.get("status") or {},
    }


def _compact_user_page(data: dict[str, Any]) -> dict[str, Any]:
    page = _as_page(data)
    page["items"] = [_compact_user(item) for item in page["items"] if isinstance(item, dict)]
    return page


def _compact_department_page(data: dict[str, Any]) -> dict[str, Any]:
    page = _as_page(data)
    page["items"] = [
        _compact_department(item) for item in page["items"] if isinstance(item, dict)
    ]
    return page


def _department_token_for_next(
    department: dict[str, Any],
    department_id_type: str,
) -> str:
    if department_id_type == "open_department_id":
        return _first_str(department, "open_department_id", "department_id")
    return _first_str(department, "department_id", "open_department_id")


def _normalize_text(value: Any, *, case_sensitive: bool) -> str:
    text = str(value or "").strip()
    return text if case_sensitive else text.lower()


def _text_matches(
    target: str,
    candidate: str,
    *,
    exact_match: bool,
    case_sensitive: bool,
) -> bool:
    target_norm = _normalize_text(target, case_sensitive=case_sensitive)
    candidate_norm = _normalize_text(candidate, case_sensitive=case_sensitive)

    if not target_norm or not candidate_norm:
        return False

    if exact_match:
        return candidate_norm == target_norm

    return target_norm in candidate_norm


def _user_matches(
    user: dict[str, Any],
    *,
    name: str,
    email: str,
    mobile: str,
    employee_no: str,
    exact_match: bool,
    case_sensitive: bool,
) -> bool:
    checks: list[bool] = []

    if name:
        checks.append(
            any(
                _text_matches(
                    name,
                    user.get(field, ""),
                    exact_match=exact_match,
                    case_sensitive=case_sensitive,
                )
                for field in ("name", "en_name", "nickname")
            )
        )

    if email:
        checks.append(
            _text_matches(
                email,
                user.get("email", ""),
                exact_match=True,
                case_sensitive=False,
            )
        )

    if mobile:
        checks.append(
            _text_matches(
                mobile,
                user.get("mobile", ""),
                exact_match=True,
                case_sensitive=True,
            )
        )

    if employee_no:
        checks.append(
            _text_matches(
                employee_no,
                user.get("employee_no", ""),
                exact_match=True,
                case_sensitive=True,
            )
        )

    return bool(checks) and all(checks)


def _fetch_users_by_department_page(
    department_id: str,
    *,
    page_size: int,
    page_token: str | None,
    user_id_type: str,
    department_id_type: str,
    user_access_token: str | None,
) -> dict[str, Any]:
    return feishu_request(
        "GET",
        f"{CONTACT_PREFIX}/users/find_by_department",
        queries=_clean(
            {
                "department_id": department_id,
                "page_size": page_size,
                "page_token": page_token,
                "user_id_type": user_id_type,
                "department_id_type": department_id_type,
            }
        ),
        user_access_token=user_access_token,
    )


def _fetch_child_departments_page(
    department_id: str,
    *,
    page_size: int,
    page_token: str | None,
    user_id_type: str,
    department_id_type: str,
    user_access_token: str | None,
) -> dict[str, Any]:
    return feishu_request(
        "GET",
        f"{CONTACT_PREFIX}/departments/{_path(department_id)}/children",
        queries=_clean(
            {
                "fetch_child": "false",
                "page_size": page_size,
                "page_token": page_token,
                "user_id_type": user_id_type,
                "department_id_type": department_id_type,
            }
        ),
        user_access_token=user_access_token,
    )


def register(registry: ToolRegistry, event_bus: EventBus | None = None):
    @registry.register(
        ToolSpec(
            name="feishu_contact_get_user",
            description="Get one Feishu user by user_id/open_id/union_id.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "user_id_type": {
                        "type": "string",
                        "enum": ["open_id", "user_id", "union_id"],
                    },
                    "department_id_type": {
                        "type": "string",
                        "enum": ["department_id", "open_department_id"],
                    },
                    "user_access_token": {"type": "string"},
                },
                "required": ["user_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "user": {"type": "object"},
                },
                "required": ["user"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_contact_get_user(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        user_id = args["user_id"]

        data = feishu_request(
            "GET",
            f"{CONTACT_PREFIX}/users/{_path(user_id)}",
            queries=_clean(
                {
                    "user_id_type": args.get("user_id_type", "open_id"),
                    "department_id_type": args.get(
                        "department_id_type", "open_department_id"
                    ),
                }
            ),
            user_access_token=args.get("user_access_token"),
        )

        user = _compact_user(data.get("user") or {})

        ctx.emit(
            "feishu.contact.user.got",
            {
                "tool": "feishu_contact_get_user",
                "user_id": user_id,
                "open_id": user.get("open_id", ""),
                "name": user.get("name", ""),
            },
        )

        return {"user": user}

    @registry.register(
        ToolSpec(
            name="feishu_contact_batch_get_user_id",
            description="Get Feishu user IDs by emails or mobiles.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "emails": {"type": "array", "items": {"type": "string"}},
                    "mobiles": {"type": "array", "items": {"type": "string"}},
                    "user_id_type": {
                        "type": "string",
                        "enum": ["open_id", "user_id", "union_id"],
                    },
                    "user_access_token": {"type": "string"},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_contact_batch_get_user_id(
        args: dict[str, Any], ctx: Any
    ) -> dict[str, Any]:
        emails = args.get("emails") or []
        mobiles = args.get("mobiles") or []

        if not emails and not mobiles:
            raise ValueError("emails or mobiles is required")

        data = feishu_request(
            "POST",
            f"{CONTACT_PREFIX}/users/batch_get_id",
            queries=_clean({"user_id_type": args.get("user_id_type", "open_id")}),
            body=_clean(
                {
                    "emails": emails,
                    "mobiles": mobiles,
                }
            ),
            user_access_token=args.get("user_access_token"),
        )

        items = _items(data)
        result = {"items": items}

        ctx.emit(
            "feishu.contact.user_ids.batch_got",
            {
                "tool": "feishu_contact_batch_get_user_id",
                "email_count": len(emails),
                "mobile_count": len(mobiles),
                "count": len(items),
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_contact_get_department",
            description="Get one Feishu department by department_id/open_department_id.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "department_id": {"type": "string"},
                    "department_id_type": {
                        "type": "string",
                        "enum": ["department_id", "open_department_id"],
                    },
                    "user_id_type": {
                        "type": "string",
                        "enum": ["open_id", "user_id", "union_id"],
                    },
                    "user_access_token": {"type": "string"},
                },
                "required": ["department_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "department": {"type": "object"},
                },
                "required": ["department"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_contact_get_department(
        args: dict[str, Any], ctx: Any
    ) -> dict[str, Any]:
        department_id = args["department_id"]

        data = feishu_request(
            "GET",
            f"{CONTACT_PREFIX}/departments/{_path(department_id)}",
            queries=_clean(
                {
                    "department_id_type": args.get(
                        "department_id_type", "open_department_id"
                    ),
                    "user_id_type": args.get("user_id_type", "open_id"),
                }
            ),
            user_access_token=args.get("user_access_token"),
        )

        department = _compact_department(data.get("department") or {})

        ctx.emit(
            "feishu.contact.department.got",
            {
                "tool": "feishu_contact_get_department",
                "department_id": department_id,
                "name": department.get("name", ""),
                "chat_id": department.get("chat_id", ""),
            },
        )

        return {"department": department}


    @registry.register(
        ToolSpec(
            name="feishu_contact_list_child_departments",
            description="List child departments under a Feishu department. Root department_id is 0.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "department_id": {"type": "string"},
                    "fetch_child": {
                        "type": "boolean",
                        "description": "Whether to recursively fetch descendants. Default false.",
                    },
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                    "department_id_type": {
                        "type": "string",
                        "enum": ["department_id", "open_department_id"],
                    },
                    "user_id_type": {
                        "type": "string",
                        "enum": ["open_id", "user_id", "union_id"],
                    },
                    "user_access_token": {"type": "string"},
                },
                "required": ["department_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                    "has_more": {"type": "boolean"},
                    "page_token": {"type": "string"},
                },
                "required": ["items", "has_more", "page_token"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_contact_list_child_departments(
        args: dict[str, Any], ctx: Any
    ) -> dict[str, Any]:
        department_id = args["department_id"]

        data = feishu_request(
            "GET",
            f"{CONTACT_PREFIX}/departments/{_path(department_id)}/children",
            queries=_clean(
                {
                    "fetch_child": _as_bool_str(args.get("fetch_child", False)),
                    "page_size": _page_size(args),
                    "page_token": args.get("page_token"),
                    "department_id_type": args.get(
                        "department_id_type", "open_department_id"
                    ),
                    "user_id_type": args.get("user_id_type", "open_id"),
                }
            ),
            user_access_token=args.get("user_access_token"),
        )

        result = _compact_department_page(data)

        ctx.emit(
            "feishu.contact.child_departments.listed",
            {
                "tool": "feishu_contact_list_child_departments",
                "department_id": department_id,
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_contact_list_parent_departments",
            description="List parent departments of a Feishu department.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "department_id": {"type": "string"},
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                    "department_id_type": {
                        "type": "string",
                        "enum": ["department_id", "open_department_id"],
                    },
                    "user_id_type": {
                        "type": "string",
                        "enum": ["open_id", "user_id", "union_id"],
                    },
                    "user_access_token": {"type": "string"},
                },
                "required": ["department_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                    "has_more": {"type": "boolean"},
                    "page_token": {"type": "string"},
                },
                "required": ["items", "has_more", "page_token"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_contact_list_parent_departments(
        args: dict[str, Any], ctx: Any
    ) -> dict[str, Any]:
        department_id = args["department_id"]

        data = feishu_request(
            "GET",
            f"{CONTACT_PREFIX}/departments/parent",
            queries=_clean(
                {
                    "department_id": department_id,
                    "page_size": _page_size(args),
                    "page_token": args.get("page_token"),
                    "department_id_type": args.get(
                        "department_id_type", "open_department_id"
                    ),
                    "user_id_type": args.get("user_id_type", "open_id"),
                }
            ),
            user_access_token=args.get("user_access_token"),
        )

        result = _compact_department_page(data)

        ctx.emit(
            "feishu.contact.parent_departments.listed",
            {
                "tool": "feishu_contact_list_parent_departments",
                "department_id": department_id,
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_contact_list_users_by_department",
            description="List direct users under a Feishu department. Root department_id is 0.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "department_id": {"type": "string"},
                    "page_size": {"type": "integer"},
                    "page_token": {"type": "string"},
                    "department_id_type": {
                        "type": "string",
                        "enum": ["department_id", "open_department_id"],
                    },
                    "user_id_type": {
                        "type": "string",
                        "enum": ["open_id", "user_id", "union_id"],
                    },
                    "user_access_token": {"type": "string"},
                },
                "required": ["department_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                    "has_more": {"type": "boolean"},
                    "page_token": {"type": "string"},
                },
                "required": ["items", "has_more", "page_token"],
                "additionalProperties": False,
            },
        )
    )
    def feishu_contact_list_users_by_department(
        args: dict[str, Any], ctx: Any
    ) -> dict[str, Any]:
        department_id = args["department_id"]

        data = _fetch_users_by_department_page(
            department_id,
            page_size=_page_size(args),
            page_token=args.get("page_token"),
            user_id_type=args.get("user_id_type", "open_id"),
            department_id_type=args.get("department_id_type", "open_department_id"),
            user_access_token=args.get("user_access_token"),
        )

        result = _compact_user_page(data)

        ctx.emit(
            "feishu.contact.department_users.listed",
            {
                "tool": "feishu_contact_list_users_by_department",
                "department_id": department_id,
                "count": len(result["items"]),
                "has_more": result["has_more"],
            },
        )

        return result

    @registry.register(
        ToolSpec(
            name="feishu_contact_resolve_user_in_department_tree",
            description="Resolve users by locally matching name/email/mobile/employee_no while traversing a department tree with app identity.",
            mode="sync",
            kind="business",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name keyword to match against name/en_name/nickname.",
                    },
                    "email": {"type": "string"},
                    "mobile": {"type": "string"},
                    "employee_no": {"type": "string"},
                    "department_id": {
                        "type": "string",
                        "description": "Start department id. Root is 0. Default 0.",
                    },
                    "include_child_departments": {
                        "type": "boolean",
                        "description": "Whether to BFS traverse child departments. Default true.",
                    },
                    "exact_match": {
                        "type": "boolean",
                        "description": "Whether name matching requires exact equality. Default true.",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Whether name matching is case sensitive. Default false.",
                    },
                    "page_size": {"type": "integer"},
                    "max_departments": {"type": "integer"},
                    "max_users": {"type": "integer"},
                    "max_matches": {"type": "integer"},
                    "department_id_type": {
                        "type": "string",
                        "enum": ["department_id", "open_department_id"],
                    },
                    "user_id_type": {
                        "type": "string",
                        "enum": ["open_id", "user_id", "union_id"],
                    },
                    "user_access_token": {"type": "string"},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                    "matched_count": {"type": "integer"},
                    "scanned_departments": {"type": "integer"},
                    "scanned_users": {"type": "integer"},
                    "queued_departments": {"type": "integer"},
                    "truncated": {"type": "boolean"},
                    "truncated_reason": {"type": "string"},
                },
                "required": [
                    "items",
                    "matched_count",
                    "scanned_departments",
                    "scanned_users",
                    "truncated",
                ],
                "additionalProperties": False,
            },
        )
    )
    def feishu_contact_resolve_user_in_department_tree(
        args: dict[str, Any], ctx: Any
    ) -> dict[str, Any]:
        name = str(args.get("name") or "").strip()
        email = str(args.get("email") or "").strip()
        mobile = str(args.get("mobile") or "").strip()
        employee_no = str(args.get("employee_no") or "").strip()

        if not any([name, email, mobile, employee_no]):
            raise ValueError("one of name/email/mobile/employee_no is required")

        start_department_id = str(args.get("department_id") or "0")
        include_child_departments = bool(args.get("include_child_departments", True))
        exact_match = bool(args.get("exact_match", True))
        case_sensitive = bool(args.get("case_sensitive", False))

        page_size = _page_size(args)
        user_id_type = args.get("user_id_type", "open_id")
        department_id_type = args.get("department_id_type", "open_department_id")
        user_access_token = args.get("user_access_token")

        max_departments = _as_int(
            args.get("max_departments", DEFAULT_MAX_DEPARTMENTS),
            DEFAULT_MAX_DEPARTMENTS,
            min_value=1,
            max_value=HARD_MAX_DEPARTMENTS,
        )
        max_users = _as_int(
            args.get("max_users", DEFAULT_MAX_USERS),
            DEFAULT_MAX_USERS,
            min_value=1,
            max_value=HARD_MAX_USERS,
        )
        max_matches = _as_int(
            args.get("max_matches", DEFAULT_MAX_MATCHES),
            DEFAULT_MAX_MATCHES,
            min_value=1,
            max_value=HARD_MAX_MATCHES,
        )

        matched_users: list[dict[str, Any]] = []
        visited_departments: set[str] = set()
        queue: deque[str] = deque([start_department_id])

        scanned_departments = 0
        scanned_users = 0
        truncated = False
        truncated_reason = ""

        while queue:
            if scanned_departments >= max_departments:
                truncated = True
                truncated_reason = "max_departments"
                break

            if scanned_users >= max_users:
                truncated = True
                truncated_reason = "max_users"
                break

            if len(matched_users) >= max_matches:
                truncated = True
                truncated_reason = "max_matches"
                break

            department_id = queue.popleft()
            if department_id in visited_departments:
                continue

            visited_departments.add(department_id)
            scanned_departments += 1

            user_page_token: str | None = None

            while True:
                remaining_users = max_users - scanned_users
                if remaining_users <= 0:
                    truncated = True
                    truncated_reason = "max_users"
                    break

                data = _fetch_users_by_department_page(
                    department_id,
                    page_size=min(page_size, remaining_users),
                    page_token=user_page_token,
                    user_id_type=user_id_type,
                    department_id_type=department_id_type,
                    user_access_token=user_access_token,
                )

                page = _compact_user_page(data)
                users = page["items"]

                for user in users:
                    scanned_users += 1

                    if _user_matches(
                        user,
                        name=name,
                        email=email,
                        mobile=mobile,
                        employee_no=employee_no,
                        exact_match=exact_match,
                        case_sensitive=case_sensitive,
                    ):
                        item = dict(user)
                        item["matched_department_id"] = department_id
                        matched_users.append(item)

                        if len(matched_users) >= max_matches:
                            truncated = True
                            truncated_reason = "max_matches"
                            break

                    if scanned_users >= max_users:
                        truncated = True
                        truncated_reason = "max_users"
                        break

                if truncated:
                    break

                if not page["has_more"]:
                    break

                user_page_token = page["page_token"]
                if not user_page_token:
                    break

            if truncated:
                break

            if not include_child_departments:
                continue

            child_page_token: str | None = None

            while True:
                if scanned_departments + len(queue) >= max_departments:
                    truncated = True
                    truncated_reason = "max_departments"
                    break

                data = _fetch_child_departments_page(
                    department_id,
                    page_size=page_size,
                    page_token=child_page_token,
                    user_id_type=user_id_type,
                    department_id_type=department_id_type,
                    user_access_token=user_access_token,
                )

                page = _compact_department_page(data)

                for department in page["items"]:
                    child_id = _department_token_for_next(department, department_id_type)
                    if not child_id or child_id in visited_departments:
                        continue
                    queue.append(child_id)

                    if scanned_departments + len(queue) >= max_departments:
                        truncated = True
                        truncated_reason = "max_departments"
                        break

                if truncated:
                    break

                if not page["has_more"]:
                    break

                child_page_token = page["page_token"]
                if not child_page_token:
                    break

            if truncated:
                break

        result = {
            "items": matched_users,
            "matched_count": len(matched_users),
            "scanned_departments": scanned_departments,
            "scanned_users": scanned_users,
            "queued_departments": len(queue),
            "truncated": truncated,
            "truncated_reason": truncated_reason,
        }

        ctx.emit(
            "feishu.contact.user.resolved_in_department_tree",
            {
                "tool": "feishu_contact_resolve_user_in_department_tree",
                "name": name,
                "department_id": start_department_id,
                "matched_count": result["matched_count"],
                "scanned_departments": scanned_departments,
                "scanned_users": scanned_users,
                "truncated": truncated,
                "truncated_reason": truncated_reason,
            },
        )

        return result