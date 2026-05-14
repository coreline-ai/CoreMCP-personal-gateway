from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any

from demo_mcp_suite.runtime import DemoMcpServer, object_schema, text_result, tool

_DEMO_DATE = "2026-05-14"
_LOCK = RLock()

_BASE_NOTES: dict[str, dict[str, Any]] = {
    "kv_note_001": {
        "id": "kv_note_001",
        "title": "CoreMCP gateway mental model",
        "body": (
            "CoreMCP is a local gateway that lets connected AI clients discover "
            "and call approved MCP servers without receiving upstream admin tokens."
        ),
        "tags": ["architecture", "coremcp", "mcp"],
        "source": "local-fixture",
        "created_at": "2026-05-11T20:30:00Z",
        "updated_at": "2026-05-11T20:30:00Z",
    },
    "kv_note_002": {
        "id": "kv_note_002",
        "title": "Local-first security checklist",
        "body": (
            "Re-check bearer auth on every /mcp request, keep credentials inside "
            "the vault abstraction, and never treat Mcp-Session-Id as auth."
        ),
        "tags": ["security", "vault", "local-first"],
        "source": "local-fixture",
        "created_at": "2026-05-12T09:45:00Z",
        "updated_at": "2026-05-12T09:45:00Z",
    },
    "kv_note_003": {
        "id": "kv_note_003",
        "title": "Personal Ops Desk demo script",
        "body": (
            "Start with ops_status, inspect open incidents, create an ops note, "
            "then run a safe backup fixture before showing destructive confirmation."
        ),
        "tags": ["demo", "mcp", "ops"],
        "source": "local-fixture",
        "created_at": "2026-05-13T18:10:00Z",
        "updated_at": "2026-05-13T18:10:00Z",
    },
}

_STATE: dict[str, Any] = {}
_COUNTERS: dict[str, int] = {}


def _reset_state() -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update({"notes": deepcopy(_BASE_NOTES), "deleted_notes": []})
        _COUNTERS.clear()
        _COUNTERS.update({"note": len(_BASE_NOTES) + 1, "tag_update": 1, "delete": 1})


def _next_id(kind: str, prefix: str) -> str:
    value = _COUNTERS[kind]
    _COUNTERS[kind] = value + 1
    return f"{prefix}_{value:03d}"


def _timestamp(kind: str, sequence: int) -> str:
    hour_by_kind = {"note": 12, "tag_update": 13, "delete": 14}
    hour = hour_by_kind.get(kind, 15)
    minute = min(sequence, 59)
    return f"{_DEMO_DATE}T{hour:02d}:{minute:02d}:00Z"


def _clean_text(value: Any, *, default: str = "", max_length: int = 500) -> str:
    if not isinstance(value, str):
        return default
    collapsed = " ".join(value.strip().split())
    if not collapsed:
        return default
    return collapsed[:max_length]


def _clean_body(value: Any, *, default: str = "", max_length: int = 8_000) -> str:
    if not isinstance(value, str):
        return default
    body = value.strip()
    return body[:max_length] if body else default


def _normalize_tags(value: Any, *, limit: int = 16) -> list[str]:
    if value is None:
        raw_items: list[Any] = []
    elif isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []

    tags: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, str):
            continue
        tag = " ".join(raw_item.strip().lower().replace("#", "").split())
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag[:40])
        if len(tags) >= limit:
            break
    return tags


def _bounded_limit(value: Any, *, default: int = 10, maximum: int = 50) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return min(max(value, 1), maximum)
    return default


def _excerpt(body: str, query: str, *, length: int = 180) -> str:
    if not query:
        return body[:length]
    body_lower = body.lower()
    index = body_lower.find(query.lower())
    if index == -1:
        return body[:length]
    start = max(index - 50, 0)
    end = min(start + length, len(body))
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{body[start:end]}{suffix}"


def _note_matches(note: dict[str, Any], query: str, tags: set[str], match: str) -> bool:
    if query:
        haystack = " ".join([note["title"], note["body"], " ".join(note["tags"])]).lower()
        if query.lower() not in haystack:
            return False
    if tags:
        note_tags = set(note["tags"])
        if match == "any":
            return bool(tags & note_tags)
        return tags <= note_tags
    return True


def _note_summary(note: dict[str, Any], query: str) -> dict[str, Any]:
    return {
        "id": note["id"],
        "title": note["title"],
        "tags": list(note["tags"]),
        "source": note["source"],
        "updated_at": note["updated_at"],
        "excerpt": _excerpt(note["body"], query),
    }


def _note_search(arguments: dict[str, Any]) -> dict[str, Any]:
    query = _clean_text(arguments.get("query"), default="", max_length=120)
    tags = set(_normalize_tags(arguments.get("tags"), limit=12))
    match = _clean_text(arguments.get("match"), default="all", max_length=10).lower()
    if match not in {"all", "any"}:
        match = "all"
    limit = _bounded_limit(arguments.get("limit"), default=10, maximum=50)

    with _LOCK:
        notes = deepcopy(list(_STATE["notes"].values()))

    results = [
        _note_summary(note, query)
        for note in sorted(notes, key=lambda item: item["updated_at"], reverse=True)
        if _note_matches(note, query, tags, match)
    ][:limit]

    return text_result(
        f"{len(results)} vault note(s) matched.",
        {
            "results": results,
            "count": len(results),
            "filters": {
                "query": query or None,
                "tags": sorted(tags) if tags else None,
                "match": match,
                "limit": limit,
            },
        },
    )


def _note_get(arguments: dict[str, Any]) -> dict[str, Any]:
    note_id = _clean_text(arguments.get("note_id"), default="", max_length=80)
    with _LOCK:
        note = deepcopy(_STATE["notes"].get(note_id))

    if note is None:
        return text_result(
            f"Vault note {note_id or '<missing>'} was not found.",
            {"status": "not_found", "note_id": note_id},
        )

    return text_result(
        f"Fetched vault note {note_id}.",
        {"status": "found", "note": note},
    )


def _note_create(arguments: dict[str, Any]) -> dict[str, Any]:
    title = _clean_text(arguments.get("title"), default="Untitled vault note", max_length=160)
    body = _clean_body(arguments.get("body"), default="", max_length=8_000)
    source = _clean_text(arguments.get("source"), default="manual", max_length=120)
    tags = _normalize_tags(arguments.get("tags"), limit=16)

    with _LOCK:
        sequence = _COUNTERS["note"]
        note_id = _next_id("note", "kv_note")
        timestamp = _timestamp("note", sequence)
        note = {
            "id": note_id,
            "title": title,
            "body": body,
            "tags": tags,
            "source": source,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        _STATE["notes"][note_id] = note
        note_count = len(_STATE["notes"])

    return text_result(
        f"Created vault note {note_id}.",
        {"note": deepcopy(note), "note_count": note_count},
    )


def _note_tag(arguments: dict[str, Any]) -> dict[str, Any]:
    note_id = _clean_text(arguments.get("note_id"), default="", max_length=80)
    tags = _normalize_tags(arguments.get("tags"), limit=16)
    mode = _clean_text(arguments.get("mode"), default="add", max_length=20).lower()
    if mode not in {"add", "remove", "replace"}:
        mode = "add"

    with _LOCK:
        note = _STATE["notes"].get(note_id)
        if note is None:
            return text_result(
                f"Vault note {note_id or '<missing>'} was not found.",
                {"status": "not_found", "note_id": note_id},
            )
        if not tags and mode != "replace":
            return text_result(
                "No tags were provided.",
                {"status": "validation_required", "note": deepcopy(note), "missing": ["tags"]},
            )

        before = list(note["tags"])
        if mode == "replace":
            note["tags"] = tags
        elif mode == "remove":
            remove = set(tags)
            note["tags"] = [tag for tag in note["tags"] if tag not in remove]
        else:
            existing = set(note["tags"])
            note["tags"].extend(tag for tag in tags if tag not in existing)

        sequence = _COUNTERS["tag_update"]
        _COUNTERS["tag_update"] = sequence + 1
        note["updated_at"] = _timestamp("tag_update", sequence)
        updated = deepcopy(note)

    return text_result(
        f"Updated tags for vault note {note_id}.",
        {
            "status": "updated",
            "mode": mode,
            "before": before,
            "after": updated["tags"],
            "note": updated,
        },
    )


def _note_delete(arguments: dict[str, Any]) -> dict[str, Any]:
    note_id = _clean_text(arguments.get("note_id"), default="", max_length=80)
    confirmed = arguments.get("confirm") is True

    with _LOCK:
        note = _STATE["notes"].get(note_id)
        if note is None:
            return text_result(
                f"Vault note {note_id or '<missing>'} was not found.",
                {"status": "not_found", "note_id": note_id},
            )
        if not confirmed:
            return text_result(
                f"Delete for {note_id} requires confirm=true.",
                {"status": "confirmation_required", "note_id": note_id, "requires_confirmation": True},
            )

        sequence = _COUNTERS["delete"]
        delete_id = _next_id("delete", "delete")
        deleted = _STATE["notes"].pop(note_id)
        tombstone = {
            "id": delete_id,
            "note_id": note_id,
            "title": deleted["title"],
            "deleted_at": _timestamp("delete", sequence),
            "remaining_note_count": len(_STATE["notes"]),
        }
        _STATE["deleted_notes"].append(tombstone)

    return text_result(
        f"Deleted vault note {note_id}.",
        {"status": "deleted", "deleted": deepcopy(tombstone)},
    )


_reset_state()

SERVER = DemoMcpServer(
    slug="knowledge-vault",
    service_slug="demo_knowledge",
    title="Local Knowledge Vault MCP",
    description="로컬 fixture 기반 개인 지식창고 MCP",
    tools=[
        tool(
            name="note_search",
            title="Search vault notes",
            description="로컬 지식창고 메모를 키워드와 태그로 검색합니다.",
            input_schema=object_schema(
                {
                    "query": {"type": "string", "maxLength": 120},
                    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                    "match": {"type": "string", "enum": ["all", "any"], "default": "all"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                }
            ),
            read_only=True,
        ),
        tool(
            name="note_get",
            title="Get vault note",
            description="로컬 지식창고 메모 하나를 ID로 조회합니다.",
            input_schema=object_schema(
                {"note_id": {"type": "string", "minLength": 1, "maxLength": 80}},
                required=["note_id"],
            ),
            read_only=True,
        ),
        tool(
            name="note_create",
            title="Create vault note",
            description="새 지식창고 메모를 로컬 in-memory 상태에 생성합니다.",
            input_schema=object_schema(
                {
                    "title": {"type": "string", "minLength": 1, "maxLength": 160},
                    "body": {"type": "string", "maxLength": 8000},
                    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
                    "source": {"type": "string", "maxLength": 120, "default": "manual"},
                },
                required=["title", "body"],
            ),
            read_only=False,
            idempotent=False,
        ),
        tool(
            name="note_tag",
            title="Tag vault note",
            description="기존 지식창고 메모의 태그를 추가, 제거, 또는 교체합니다.",
            input_schema=object_schema(
                {
                    "note_id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
                    "mode": {"type": "string", "enum": ["add", "remove", "replace"], "default": "add"},
                },
                required=["note_id", "tags"],
            ),
            read_only=False,
            idempotent=False,
        ),
        tool(
            name="note_delete",
            title="Delete vault note",
            description="지식창고 메모를 삭제합니다. confirm=true가 있어야 상태를 변경합니다.",
            input_schema=object_schema(
                {
                    "note_id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "confirm": {
                        "type": "boolean",
                        "const": True,
                        "description": "파괴적 작업 확인 플래그입니다.",
                    },
                },
                required=["note_id", "confirm"],
            ),
            read_only=False,
            destructive=True,
            idempotent=False,
        ),
    ],
    handlers={
        "note_search": _note_search,
        "note_get": _note_get,
        "note_create": _note_create,
        "note_tag": _note_tag,
        "note_delete": _note_delete,
    },
    reset=_reset_state,
)
