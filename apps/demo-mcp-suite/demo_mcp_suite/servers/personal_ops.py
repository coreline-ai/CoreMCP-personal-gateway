from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any

from demo_mcp_suite.runtime import DemoMcpServer, object_schema, text_result, tool

_DEMO_DATE = "2026-05-14"
_LOCK = RLock()

_BASE_SERVICES: dict[str, dict[str, Any]] = {
    "coremcp-api": {
        "name": "coremcp-api",
        "status": "healthy",
        "uptime": "6d 4h",
        "last_restart_at": "2026-05-13T21:10:00Z",
        "dependencies": ["credential-vault", "demo-mcp-suite"],
        "note": "Gateway API smoke checks are green.",
    },
    "demo-mcp-suite": {
        "name": "demo-mcp-suite",
        "status": "healthy",
        "uptime": "1d 2h",
        "last_restart_at": "2026-05-13T23:30:00Z",
        "dependencies": ["local-fixtures"],
        "note": "Eight demo MCP endpoints are registered locally.",
    },
    "backup-agent": {
        "name": "backup-agent",
        "status": "warning",
        "uptime": "13d 8h",
        "last_restart_at": "2026-05-01T08:00:00Z",
        "dependencies": ["external-drive", "local-snapshot-index"],
        "note": "Photo backup is older than the 24h target.",
    },
}

_BASE_CHECKLIST: list[dict[str, Any]] = [
    {
        "id": "ops_chk_001",
        "title": "Review overnight CoreMCP smoke result",
        "status": "open",
        "priority": "p1",
        "area": "CoreMCP",
        "due": _DEMO_DATE,
    },
    {
        "id": "ops_chk_002",
        "title": "Export credential vault recovery key",
        "status": "blocked",
        "priority": "p0",
        "area": "Security",
        "due": _DEMO_DATE,
    },
    {
        "id": "ops_chk_003",
        "title": "Confirm local backup rotation",
        "status": "done",
        "priority": "p2",
        "area": "Backups",
        "due": "2026-05-13",
    },
]

_BASE_INCIDENTS: list[dict[str, Any]] = [
    {
        "id": "inc_20260514_001",
        "severity": "sev2",
        "status": "watching",
        "service": "backup-agent",
        "summary": "Photo backup is outside the desired recovery window.",
        "opened_at": "2026-05-14T07:15:00Z",
        "next_action": "Run backup_run for the photos target after the demo.",
    },
    {
        "id": "inc_20260513_002",
        "severity": "sev3",
        "status": "resolved",
        "service": "demo-mcp-suite",
        "summary": "Demo fixture reload caused one stale session during setup.",
        "opened_at": "2026-05-13T22:05:00Z",
        "resolved_at": "2026-05-13T22:20:00Z",
        "next_action": "Keep _test/reset-state in the smoke flow.",
    },
]

_BACKUP_TARGETS: dict[str, dict[str, Any]] = {
    "coremcp-config": {
        "display_name": "CoreMCP config",
        "files": 42,
        "bytes": 524_288,
        "destination": "local://backups/coremcp-config",
    },
    "documents": {
        "display_name": "Documents",
        "files": 318,
        "bytes": 48_234_512,
        "destination": "local://backups/documents",
    },
    "photos": {
        "display_name": "Photos",
        "files": 1_204,
        "bytes": 2_843_115_520,
        "destination": "local://backups/photos",
    },
}

_STATE: dict[str, Any] = {}
_COUNTERS: dict[str, int] = {}


def _reset_state() -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update(
            {
                "services": deepcopy(_BASE_SERVICES),
                "checklist": deepcopy(_BASE_CHECKLIST),
                "incidents": deepcopy(_BASE_INCIDENTS),
                "notes": {},
                "backup_runs": [],
                "restart_events": [],
            }
        )
        _COUNTERS.clear()
        _COUNTERS.update({"note": 1, "backup": 1, "restart": 1})


def _next_id(kind: str, prefix: str) -> str:
    value = _COUNTERS[kind]
    _COUNTERS[kind] = value + 1
    return f"{prefix}_{value:03d}"


def _timestamp(kind: str, sequence: int) -> str:
    hour_by_kind = {"note": 9, "backup": 10, "restart": 11}
    hour = hour_by_kind.get(kind, 12)
    minute = min(sequence, 59)
    return f"{_DEMO_DATE}T{hour:02d}:{minute:02d}:00Z"


def _clean_text(value: Any, *, default: str = "", max_length: int = 500) -> str:
    if not isinstance(value, str):
        return default
    collapsed = " ".join(value.strip().split())
    if not collapsed:
        return default
    return collapsed[:max_length]


def _clean_body(value: Any, *, default: str = "", max_length: int = 4_000) -> str:
    if not isinstance(value, str):
        return default
    body = value.strip()
    return body[:max_length] if body else default


def _string_list(value: Any, *, allowed: set[str] | None = None, limit: int = 12) -> list[str]:
    if value is None:
        raw_items: list[Any] = []
    elif isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []

    items: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, str):
            continue
        item = " ".join(raw_item.strip().lower().split())
        if not item or item in seen:
            continue
        if allowed is not None and item not in allowed:
            continue
        seen.add(item)
        items.append(item)
        if len(items) >= limit:
            break
    return items


def _bounded_limit(value: Any, *, default: int = 20, maximum: int = 100) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return min(max(value, 1), maximum)
    return default


def _ops_status(arguments: dict[str, Any]) -> dict[str, Any]:
    include_services = arguments.get("include_services", True) is not False
    with _LOCK:
        services = deepcopy(list(_STATE["services"].values()))
        checklist = deepcopy(_STATE["checklist"])
        incidents = deepcopy(_STATE["incidents"])

    service_counts: dict[str, int] = {}
    for service in services:
        status = service["status"]
        service_counts[status] = service_counts.get(status, 0) + 1

    open_checklist = [item for item in checklist if item["status"] != "done"]
    active_incidents = [item for item in incidents if item["status"] != "resolved"]
    overall_status = "warning" if active_incidents or service_counts.get("warning") else "healthy"

    structured: dict[str, Any] = {
        "generated_at": f"{_DEMO_DATE}T09:00:00Z",
        "overall_status": overall_status,
        "summary": "Personal ops desk is stable with one backup follow-up.",
        "service_counts": service_counts,
        "open_checklist_count": len(open_checklist),
        "active_incident_count": len(active_incidents),
        "next_actions": [item["title"] for item in open_checklist[:3]],
    }
    if include_services:
        structured["services"] = services

    return text_result(
        f"Personal Ops Desk status: {overall_status}; "
        f"{len(open_checklist)} checklist items need attention.",
        structured,
    )


def _ops_checklist(arguments: dict[str, Any]) -> dict[str, Any]:
    allowed_statuses = {"open", "blocked", "done"}
    statuses = set(_string_list(arguments.get("status"), allowed=allowed_statuses, limit=3))
    limit = _bounded_limit(arguments.get("limit"), default=20, maximum=50)

    with _LOCK:
        items = deepcopy(_STATE["checklist"])

    if statuses:
        items = [item for item in items if item["status"] in statuses]
    items = items[:limit]

    return text_result(
        f"{len(items)} ops checklist item(s) matched.",
        {
            "items": items,
            "count": len(items),
            "filters": {"status": sorted(statuses) if statuses else None, "limit": limit},
        },
    )


def _incident_list(arguments: dict[str, Any]) -> dict[str, Any]:
    allowed_statuses = {"watching", "resolved"}
    allowed_severities = {"sev1", "sev2", "sev3"}
    statuses = set(_string_list(arguments.get("status"), allowed=allowed_statuses, limit=2))
    severities = set(_string_list(arguments.get("severity"), allowed=allowed_severities, limit=3))
    limit = _bounded_limit(arguments.get("limit"), default=20, maximum=50)

    with _LOCK:
        incidents = deepcopy(_STATE["incidents"])

    if statuses:
        incidents = [item for item in incidents if item["status"] in statuses]
    if severities:
        incidents = [item for item in incidents if item["severity"] in severities]
    incidents = incidents[:limit]

    return text_result(
        f"{len(incidents)} incident(s) matched.",
        {
            "incidents": incidents,
            "count": len(incidents),
            "filters": {
                "status": sorted(statuses) if statuses else None,
                "severity": sorted(severities) if severities else None,
                "limit": limit,
            },
        },
    )


def _note_create(arguments: dict[str, Any]) -> dict[str, Any]:
    title = _clean_text(arguments.get("title"), default="Untitled ops note", max_length=120)
    body = _clean_body(arguments.get("body"), default="", max_length=4_000)
    area = _clean_text(arguments.get("area"), default="General", max_length=80)
    tags = _string_list(arguments.get("tags"), limit=8)

    with _LOCK:
        sequence = _COUNTERS["note"]
        note_id = _next_id("note", "ops_note")
        note = {
            "id": note_id,
            "title": title,
            "body": body,
            "area": area,
            "tags": tags,
            "created_at": _timestamp("note", sequence),
        }
        _STATE["notes"][note_id] = note
        note_count = len(_STATE["notes"])

    return text_result(
        f"Created ops note {note_id}.",
        {"note": deepcopy(note), "note_count": note_count},
    )


def _backup_run(arguments: dict[str, Any]) -> dict[str, Any]:
    target = _clean_text(arguments.get("target"), default="coremcp-config", max_length=80).lower()
    if target not in _BACKUP_TARGETS:
        target = "coremcp-config"
    mode = _clean_text(arguments.get("mode"), default="incremental", max_length=40).lower()
    if mode not in {"incremental", "full", "verify-only"}:
        mode = "incremental"

    target_info = _BACKUP_TARGETS[target]
    with _LOCK:
        sequence = _COUNTERS["backup"]
        backup_id = _next_id("backup", "backup")
        run = {
            "id": backup_id,
            "target": target,
            "target_name": target_info["display_name"],
            "mode": mode,
            "status": "completed",
            "started_at": _timestamp("backup", sequence),
            "completed_at": _timestamp("backup", sequence + 1),
            "files_checked": target_info["files"],
            "bytes_written": 0 if mode == "verify-only" else target_info["bytes"],
            "destination": target_info["destination"],
        }
        _STATE["backup_runs"].append(run)
        if target == "photos":
            _STATE["services"]["backup-agent"]["status"] = "healthy"
            _STATE["services"]["backup-agent"]["note"] = "Photo backup completed during demo."
        run_count = len(_STATE["backup_runs"])

    return text_result(
        f"Backup {backup_id} completed for {target_info['display_name']}.",
        {"backup": deepcopy(run), "run_count": run_count},
    )


def _service_restart(arguments: dict[str, Any]) -> dict[str, Any]:
    service_name = _clean_text(arguments.get("service"), default="", max_length=80)
    reason = _clean_text(arguments.get("reason"), default="No reason provided", max_length=240)
    confirmed = arguments.get("confirm") is True

    with _LOCK:
        if service_name not in _STATE["services"]:
            return text_result(
                f"Service {service_name or '<missing>'} was not found.",
                {
                    "status": "not_found",
                    "service": service_name,
                    "known_services": sorted(_STATE["services"]),
                },
            )
        if not confirmed:
            return text_result(
                f"Restart for {service_name} requires confirm=true.",
                {
                    "status": "confirmation_required",
                    "service": service_name,
                    "requires_confirmation": True,
                },
            )

        sequence = _COUNTERS["restart"]
        restart_id = _next_id("restart", "restart")
        restarted_at = _timestamp("restart", sequence)
        service = _STATE["services"][service_name]
        previous_status = service["status"]
        service["status"] = "healthy"
        service["uptime"] = "0m"
        service["last_restart_at"] = restarted_at
        service["note"] = f"Restarted during demo: {reason}"
        event = {
            "id": restart_id,
            "service": service_name,
            "previous_status": previous_status,
            "status": "completed",
            "reason": reason,
            "restarted_at": restarted_at,
        }
        _STATE["restart_events"].append(event)

    return text_result(
        f"Restarted {service_name} ({restart_id}).",
        {"restart": deepcopy(event), "service": deepcopy(service)},
    )


_reset_state()

SERVER = DemoMcpServer(
    slug="personal-ops",
    service_slug="demo_ops",
    title="Personal Ops Desk MCP",
    description="로컬 fixture 기반 개인 운영 데스크 MCP",
    tools=[
        tool(
            name="ops_status",
            title="Ops status",
            description="개인 운영 데스크의 현재 상태와 다음 액션을 요약합니다.",
            input_schema=object_schema(
                {
                    "include_services": {
                        "type": "boolean",
                        "description": "서비스별 상태를 structuredContent에 포함할지 여부입니다.",
                        "default": True,
                    }
                }
            ),
            read_only=True,
        ),
        tool(
            name="ops_checklist",
            title="Ops checklist",
            description="운영 체크리스트를 상태별로 조회합니다.",
            input_schema=object_schema(
                {
                    "status": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["open", "blocked", "done"]},
                        "description": "조회할 체크리스트 상태입니다.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 20,
                    },
                }
            ),
            read_only=True,
        ),
        tool(
            name="incident_list",
            title="Incident list",
            description="개인 운영 이슈와 후속 조치를 조회합니다.",
            input_schema=object_schema(
                {
                    "status": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["watching", "resolved"]},
                    },
                    "severity": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["sev1", "sev2", "sev3"]},
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 20,
                    },
                }
            ),
            read_only=True,
        ),
        tool(
            name="note_create",
            title="Create ops note",
            description="개인 운영 메모를 로컬 in-memory 상태에 생성합니다.",
            input_schema=object_schema(
                {
                    "title": {"type": "string", "minLength": 1, "maxLength": 120},
                    "body": {"type": "string", "maxLength": 4000},
                    "area": {"type": "string", "maxLength": 80, "default": "General"},
                    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                },
                required=["title"],
            ),
            read_only=False,
            idempotent=False,
        ),
        tool(
            name="backup_run",
            title="Run local backup",
            description="가상 로컬 백업 작업을 실행하고 실행 이력을 남깁니다.",
            input_schema=object_schema(
                {
                    "target": {
                        "type": "string",
                        "enum": sorted(_BACKUP_TARGETS),
                        "default": "coremcp-config",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["incremental", "full", "verify-only"],
                        "default": "incremental",
                    },
                }
            ),
            read_only=False,
            idempotent=False,
        ),
        tool(
            name="service_restart",
            title="Restart service",
            description="가상 서비스를 재시작합니다. confirm=true가 있어야 상태를 변경합니다.",
            input_schema=object_schema(
                {
                    "service": {
                        "type": "string",
                        "enum": sorted(_BASE_SERVICES),
                        "description": "재시작할 서비스 이름입니다.",
                    },
                    "reason": {"type": "string", "maxLength": 240},
                    "confirm": {
                        "type": "boolean",
                        "const": True,
                        "description": "파괴적 작업 확인 플래그입니다.",
                    },
                },
                required=["service", "confirm"],
            ),
            read_only=False,
            destructive=True,
            idempotent=False,
        ),
    ],
    handlers={
        "ops_status": _ops_status,
        "ops_checklist": _ops_checklist,
        "incident_list": _incident_list,
        "note_create": _note_create,
        "backup_run": _backup_run,
        "service_restart": _service_restart,
    },
    reset=_reset_state,
)
