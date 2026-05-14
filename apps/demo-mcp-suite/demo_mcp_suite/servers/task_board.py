from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from demo_mcp_suite.runtime import DemoMcpServer, object_schema, text_result, tool

ACTIVE_STATUSES = ("todo", "in_progress", "blocked", "done")
LIST_STATUSES = (*ACTIVE_STATUSES, "archived")
PRIORITIES = ("low", "medium", "high", "urgent")

_TASK_FIXTURES: list[dict[str, Any]] = [
    {
        "id": "task-001",
        "title": "Codex CLI smoke path 확인",
        "description": "CoreMCP /mcp 경로에서 tools/list와 tools/call 왕복을 검증한다.",
        "status": "in_progress",
        "priority": "urgent",
        "owner": "hwan",
        "tags": ["coremcp", "smoke"],
        "due_date": "2026-05-15",
        "created_at": "2026-05-11T22:18:42Z",
        "updated_at": "2026-05-14T09:00:00Z",
        "archived": False,
        "activity": ["P0 demo scenario created"],
    },
    {
        "id": "task-002",
        "title": "Demo MCP registry fixture 정리",
        "description": "개인 gateway demo에 필요한 로컬 MCP 서비스를 fixture 기반으로 등록한다.",
        "status": "todo",
        "priority": "high",
        "owner": "worker-a",
        "tags": ["demo", "registry"],
        "due_date": "2026-05-16",
        "created_at": "2026-05-12T10:00:00Z",
        "updated_at": "2026-05-12T10:00:00Z",
        "archived": False,
        "activity": [],
    },
    {
        "id": "task-003",
        "title": "Admin UI copy 용어 점검",
        "description": "도구함, 연결된 AI client, MCP 추가/등록 용어가 화면에서 일관적인지 확인한다.",
        "status": "blocked",
        "priority": "medium",
        "owner": "design",
        "tags": ["copy", "admin-ui"],
        "due_date": None,
        "created_at": "2026-05-13T14:30:00Z",
        "updated_at": "2026-05-13T16:00:00Z",
        "archived": False,
        "activity": ["Waiting for P2 screen inventory"],
    },
]

_TASKS: dict[str, dict[str, Any]] = {}
_NEXT_TASK_NUMBER = 1


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _next_number_from(tasks: dict[str, dict[str, Any]]) -> int:
    highest = 0
    for task_id in tasks:
        prefix, _, suffix = task_id.partition("-")
        if prefix == "task" and suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest + 1


def _reset_state() -> None:
    global _TASKS, _NEXT_TASK_NUMBER
    _TASKS = {task["id"]: deepcopy(task) for task in _TASK_FIXTURES}
    _NEXT_TASK_NUMBER = _next_number_from(_TASKS)


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(task)


def _error_result(code: str, message: str, **details: Any) -> dict[str, Any]:
    structured: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        structured["error"]["details"] = details
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": structured,
        "isError": True,
    }


def _required_text(args: dict[str, Any], key: str, *, max_length: int = 240) -> tuple[str | None, dict[str, Any] | None]:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        return None, _error_result("invalid_argument", f"'{key}' must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        return None, _error_result("invalid_argument", f"'{key}' must be {max_length} characters or fewer")
    return normalized, None


def _optional_text(
    args: dict[str, Any],
    key: str,
    *,
    default: str | None = None,
    max_length: int = 500,
) -> tuple[str | None, dict[str, Any] | None]:
    value = args.get(key, default)
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, _error_result("invalid_argument", f"'{key}' must be a string")
    normalized = value.strip()
    if len(normalized) > max_length:
        return None, _error_result("invalid_argument", f"'{key}' must be {max_length} characters or fewer")
    return normalized, None


def _optional_int(args: dict[str, Any], key: str, *, default: int, minimum: int, maximum: int) -> tuple[int | None, dict[str, Any] | None]:
    value = args.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        return None, _error_result("invalid_argument", f"'{key}' must be an integer")
    if value < minimum or value > maximum:
        return None, _error_result("invalid_argument", f"'{key}' must be between {minimum} and {maximum}")
    return value, None


def _normalize_tags(value: Any) -> tuple[list[str] | None, dict[str, Any] | None]:
    if value is None:
        return [], None
    if not isinstance(value, list):
        return None, _error_result("invalid_argument", "'tags' must be an array of strings")
    tags: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None, _error_result("invalid_argument", "'tags' must contain only strings")
        tag = item.strip().lower()
        if tag and tag not in tags:
            tags.append(tag)
    if len(tags) > 12:
        return None, _error_result("invalid_argument", "'tags' can include at most 12 values")
    return tags, None


def _validate_due_date(value: Any) -> tuple[str | None, dict[str, Any] | None]:
    if value is None:
        return None, None
    if not isinstance(value, str) or not value.strip():
        return None, _error_result("invalid_argument", "'due_date' must be an ISO date string like 2026-05-14")
    normalized = value.strip()
    try:
        datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError:
        return None, _error_result("invalid_argument", "'due_date' must be an ISO date string like 2026-05-14")
    return normalized, None


def _task_line(task: dict[str, Any]) -> str:
    archive_suffix = " archived" if task.get("archived") else ""
    return f"{task['id']} [{task['status']}{archive_suffix}] {task['title']}"


def task_list(args: dict[str, Any]) -> dict[str, Any]:
    status, status_error = _optional_text(args, "status", max_length=40)
    if status_error:
        return status_error
    if status is not None and status not in LIST_STATUSES:
        return _error_result("invalid_argument", f"'status' must be one of {', '.join(LIST_STATUSES)}")

    owner, owner_error = _optional_text(args, "owner", max_length=80)
    if owner_error:
        return owner_error
    tag, tag_error = _optional_text(args, "tag", max_length=80)
    if tag_error:
        return tag_error
    include_archived = args.get("include_archived", False)
    if not isinstance(include_archived, bool):
        return _error_result("invalid_argument", "'include_archived' must be a boolean")
    limit, limit_error = _optional_int(args, "limit", default=50, minimum=1, maximum=100)
    if limit_error:
        return limit_error

    normalized_owner = owner.lower() if owner else None
    normalized_tag = tag.lower() if tag else None
    matches: list[dict[str, Any]] = []
    for task in _TASKS.values():
        is_archived = bool(task.get("archived"))
        if status == "archived":
            if not is_archived:
                continue
        elif status is not None and task["status"] != status:
            continue
        elif is_archived and not include_archived:
            continue
        if normalized_owner and task["owner"].lower() != normalized_owner:
            continue
        if normalized_tag and normalized_tag not in task["tags"]:
            continue
        matches.append(_public_task(task))

    matches.sort(key=lambda item: (item.get("archived", False), item.get("due_date") or "9999-12-31", item["id"]))
    limited = matches[:limit]
    lines = "\n".join(_task_line(task) for task in limited) or "No tasks matched."
    return text_result(
        f"{len(limited)} task(s) matched.\n{lines}",
        {
            "tasks": limited,
            "count": len(limited),
            "total_matches": len(matches),
            "filters": {
                "status": status,
                "owner": owner,
                "tag": tag,
                "include_archived": include_archived,
                "limit": limit,
            },
        },
    )


def task_get(args: dict[str, Any]) -> dict[str, Any]:
    task_id, task_id_error = _required_text(args, "task_id", max_length=40)
    if task_id_error:
        return task_id_error
    task = _TASKS.get(task_id)
    if task is None:
        return _error_result("not_found", f"Task '{task_id}' was not found", task_id=task_id)
    return text_result(_task_line(task), {"task": _public_task(task)})


def task_create(args: dict[str, Any]) -> dict[str, Any]:
    global _NEXT_TASK_NUMBER

    title, title_error = _required_text(args, "title", max_length=160)
    if title_error:
        return title_error
    description, description_error = _optional_text(args, "description", default="", max_length=1200)
    if description_error:
        return description_error
    owner, owner_error = _optional_text(args, "owner", default="unassigned", max_length=80)
    if owner_error:
        return owner_error
    priority, priority_error = _optional_text(args, "priority", default="medium", max_length=20)
    if priority_error:
        return priority_error
    if priority not in PRIORITIES:
        return _error_result("invalid_argument", f"'priority' must be one of {', '.join(PRIORITIES)}")
    status, status_error = _optional_text(args, "status", default="todo", max_length=40)
    if status_error:
        return status_error
    if status not in ACTIVE_STATUSES:
        return _error_result("invalid_argument", f"'status' must be one of {', '.join(ACTIVE_STATUSES)}")
    tags, tags_error = _normalize_tags(args.get("tags"))
    if tags_error:
        return tags_error
    due_date, due_date_error = _validate_due_date(args.get("due_date"))
    if due_date_error:
        return due_date_error

    task_id = f"task-{_NEXT_TASK_NUMBER:03d}"
    _NEXT_TASK_NUMBER += 1
    now = _utc_now()
    task = {
        "id": task_id,
        "title": title,
        "description": description or "",
        "status": status,
        "priority": priority,
        "owner": owner or "unassigned",
        "tags": tags or [],
        "due_date": due_date,
        "created_at": now,
        "updated_at": now,
        "archived": False,
        "activity": ["Created through task_create"],
    }
    _TASKS[task_id] = task
    return text_result(f"Created {_task_line(task)}", {"task": _public_task(task), "created": True})


def task_update_status(args: dict[str, Any]) -> dict[str, Any]:
    task_id, task_id_error = _required_text(args, "task_id", max_length=40)
    if task_id_error:
        return task_id_error
    status, status_error = _required_text(args, "status", max_length=40)
    if status_error:
        return status_error
    if status not in ACTIVE_STATUSES:
        return _error_result("invalid_argument", f"'status' must be one of {', '.join(ACTIVE_STATUSES)}")
    note, note_error = _optional_text(args, "note", max_length=240)
    if note_error:
        return note_error

    task = _TASKS.get(task_id)
    if task is None:
        return _error_result("not_found", f"Task '{task_id}' was not found", task_id=task_id)
    if task.get("archived"):
        return _error_result("invalid_state", f"Task '{task_id}' is archived and cannot be updated", task_id=task_id)

    old_status = task["status"]
    changed = old_status != status
    task["status"] = status
    task["updated_at"] = _utc_now()
    task.setdefault("activity", []).append(note or f"Status changed from {old_status} to {status}")
    return text_result(
        f"Updated {task_id} status from {old_status} to {status}.",
        {"task": _public_task(task), "changed": changed, "old_status": old_status, "new_status": status},
    )


def task_archive(args: dict[str, Any]) -> dict[str, Any]:
    task_id, task_id_error = _required_text(args, "task_id", max_length=40)
    if task_id_error:
        return task_id_error
    reason, reason_error = _optional_text(args, "reason", max_length=240)
    if reason_error:
        return reason_error

    task = _TASKS.get(task_id)
    if task is None:
        return _error_result("not_found", f"Task '{task_id}' was not found", task_id=task_id)

    already_archived = bool(task.get("archived"))
    if not already_archived:
        task["archived"] = True
        task["status"] = "archived"
        task["archived_at"] = _utc_now()
        task["archive_reason"] = reason or "No reason provided"
        task["updated_at"] = task["archived_at"]
        task.setdefault("activity", []).append(f"Archived: {task['archive_reason']}")

    return text_result(
        f"Archived {task_id}." if not already_archived else f"{task_id} was already archived.",
        {"task": _public_task(task), "archived": True, "already_archived": already_archived},
    )


TOOLS = [
    tool(
        name="task_list",
        title="List project tasks",
        description="List in-memory project tasks with optional status, owner, tag, and archive filters.",
        input_schema=object_schema(
            {
                "status": {"type": "string", "enum": list(LIST_STATUSES)},
                "owner": {"type": "string"},
                "tag": {"type": "string"},
                "include_archived": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
            }
        ),
        read_only=True,
    ),
    tool(
        name="task_get",
        title="Get task details",
        description="Return a single task by ID, including archived tasks.",
        input_schema=object_schema({"task_id": {"type": "string"}}, required=["task_id"]),
        read_only=True,
    ),
    tool(
        name="task_create",
        title="Create task",
        description="Create a new in-memory project task. Resetting the demo restores fixture state.",
        input_schema=object_schema(
            {
                "title": {"type": "string", "minLength": 1, "maxLength": 160},
                "description": {"type": "string", "maxLength": 1200},
                "owner": {"type": "string", "default": "unassigned"},
                "priority": {"type": "string", "enum": list(PRIORITIES), "default": "medium"},
                "status": {"type": "string", "enum": list(ACTIVE_STATUSES), "default": "todo"},
                "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                "due_date": {"type": ["string", "null"], "description": "ISO date, e.g. 2026-05-14"},
            },
            required=["title"],
        ),
        read_only=False,
        idempotent=False,
    ),
    tool(
        name="task_update_status",
        title="Update task status",
        description="Move an active task to todo, in_progress, blocked, or done.",
        input_schema=object_schema(
            {
                "task_id": {"type": "string"},
                "status": {"type": "string", "enum": list(ACTIVE_STATUSES)},
                "note": {"type": "string", "maxLength": 240},
            },
            required=["task_id", "status"],
        ),
        read_only=False,
        idempotent=True,
    ),
    tool(
        name="task_archive",
        title="Archive task",
        description="Archive a task so normal list calls hide it. This is a destructive demo mutation.",
        input_schema=object_schema(
            {
                "task_id": {"type": "string"},
                "reason": {"type": "string", "maxLength": 240},
            },
            required=["task_id"],
        ),
        read_only=False,
        destructive=True,
        idempotent=True,
    ),
]

HANDLERS = {
    "task_list": task_list,
    "task_get": task_get,
    "task_create": task_create,
    "task_update_status": task_update_status,
    "task_archive": task_archive,
}

_reset_state()

SERVER = DemoMcpServer(
    slug="task-board",
    service_slug="demo_tasks",
    title="Project Task Board MCP",
    description="가상의 프로젝트 태스크 보드 MCP",
    tools=TOOLS,
    handlers=HANDLERS,
    reset=_reset_state,
)
