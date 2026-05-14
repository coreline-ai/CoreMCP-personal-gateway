from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from demo_mcp_suite.runtime import DemoMcpServer, object_schema, text_result, tool

_BOOKMARK_FIXTURES: list[dict[str, Any]] = [
    {
        "id": "bookmark-001",
        "title": "CoreMCP local gateway architecture note",
        "url": "https://fixtures.example.test/coremcp/local-gateway",
        "tags": ["coremcp", "architecture", "mcp"],
        "summary": "Static fixture describing a personal MCP gateway that routes clients to local demo services.",
        "notes": "Useful for explaining the P0 smoke path without any external dependency.",
        "captured_at": "2026-05-11T22:20:00Z",
        "source": "fixture",
    },
    {
        "id": "bookmark-002",
        "title": "MCP tool annotation checklist",
        "url": "https://fixtures.example.test/mcp/tool-annotations",
        "tags": ["mcp", "tools", "safety"],
        "summary": "Checklist for readOnlyHint, destructiveHint, idempotentHint, and openWorldHint demo behavior.",
        "notes": "Use when validating tools/list payloads in CoreMCP demos.",
        "captured_at": "2026-05-12T09:45:00Z",
        "source": "fixture",
    },
    {
        "id": "bookmark-003",
        "title": "Local-first research workflow",
        "url": "https://fixtures.example.test/research/local-first",
        "tags": ["research", "local-first", "bookmarks"],
        "summary": "Fixture article about organizing saved links, summaries, and tags without crawling the web.",
        "notes": "Shows how Bookmark Research MCP stays deterministic for tests.",
        "captured_at": "2026-05-13T13:10:00Z",
        "source": "fixture",
    },
]

_BOOKMARKS: dict[str, dict[str, Any]] = {}
_DELETED_BOOKMARK_IDS: set[str] = set()
_NEXT_BOOKMARK_NUMBER = 1


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _next_number_from(bookmarks: dict[str, dict[str, Any]]) -> int:
    highest = 0
    for bookmark_id in bookmarks:
        prefix, _, suffix = bookmark_id.partition("-")
        if prefix == "bookmark" and suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest + 1


def _reset_state() -> None:
    global _BOOKMARKS, _DELETED_BOOKMARK_IDS, _NEXT_BOOKMARK_NUMBER
    _BOOKMARKS = {bookmark["id"]: deepcopy(bookmark) for bookmark in _BOOKMARK_FIXTURES}
    _DELETED_BOOKMARK_IDS = set()
    _NEXT_BOOKMARK_NUMBER = _next_number_from(_BOOKMARKS)


def _public_bookmark(bookmark: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(bookmark)


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
    max_length: int = 1000,
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
    if len(tags) > 16:
        return None, _error_result("invalid_argument", "'tags' can include at most 16 values")
    return tags, None


def _validate_url(url: str) -> tuple[str | None, dict[str, Any] | None]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, _error_result("invalid_argument", "'url' must be an absolute http(s) URL")
    return url, None


def _matches_query(bookmark: dict[str, Any], query: str) -> bool:
    needle = query.lower()
    searchable = " ".join(
        [
            bookmark["title"],
            bookmark["url"],
            bookmark.get("summary", ""),
            bookmark.get("notes", ""),
            " ".join(bookmark.get("tags", [])),
        ]
    ).lower()
    return needle in searchable


def _bookmark_line(bookmark: dict[str, Any]) -> str:
    tags = ", ".join(bookmark["tags"])
    return f"{bookmark['id']} {bookmark['title']} ({tags})"


def bookmark_search(args: dict[str, Any]) -> dict[str, Any]:
    query, query_error = _required_text(args, "query", max_length=160)
    if query_error:
        return query_error
    tags, tags_error = _normalize_tags(args.get("tags"))
    if tags_error:
        return tags_error
    limit, limit_error = _optional_int(args, "limit", default=10, minimum=1, maximum=50)
    if limit_error:
        return limit_error

    matches: list[dict[str, Any]] = []
    for bookmark in _BOOKMARKS.values():
        if not _matches_query(bookmark, query):
            continue
        if tags and not set(tags).issubset(set(bookmark["tags"])):
            continue
        matches.append(_public_bookmark(bookmark))

    matches.sort(key=lambda item: (item["title"].lower(), item["id"]))
    limited = matches[:limit]
    lines = "\n".join(_bookmark_line(bookmark) for bookmark in limited) or "No bookmarks matched."
    return text_result(
        f"{len(limited)} bookmark(s) matched for '{query}'.\n{lines}",
        {
            "bookmarks": limited,
            "count": len(limited),
            "total_matches": len(matches),
            "query": query,
            "tags": tags,
            "limit": limit,
        },
    )


def bookmark_list_by_tag(args: dict[str, Any]) -> dict[str, Any]:
    tag, tag_error = _required_text(args, "tag", max_length=80)
    if tag_error:
        return tag_error
    normalized_tag = tag.lower()
    limit, limit_error = _optional_int(args, "limit", default=25, minimum=1, maximum=100)
    if limit_error:
        return limit_error

    matches = [_public_bookmark(bookmark) for bookmark in _BOOKMARKS.values() if normalized_tag in bookmark["tags"]]
    matches.sort(key=lambda item: (item["captured_at"], item["id"]))
    limited = matches[:limit]
    lines = "\n".join(_bookmark_line(bookmark) for bookmark in limited) or "No bookmarks matched."
    return text_result(
        f"{len(limited)} bookmark(s) tagged '{normalized_tag}'.\n{lines}",
        {"bookmarks": limited, "count": len(limited), "total_matches": len(matches), "tag": normalized_tag, "limit": limit},
    )


def bookmark_summarize_stub(args: dict[str, Any]) -> dict[str, Any]:
    bookmark_id, bookmark_id_error = _required_text(args, "bookmark_id", max_length=60)
    if bookmark_id_error:
        return bookmark_id_error
    style, style_error = _optional_text(args, "style", default="short", max_length=20)
    if style_error:
        return style_error
    if style not in {"short", "bullets"}:
        return _error_result("invalid_argument", "'style' must be 'short' or 'bullets'")

    bookmark = _BOOKMARKS.get(bookmark_id)
    if bookmark is None:
        return _error_result("not_found", f"Bookmark '{bookmark_id}' was not found", bookmark_id=bookmark_id)

    summary = bookmark["summary"]
    if style == "bullets":
        text = f"- {bookmark['title']}\n- {summary}\n- Tags: {', '.join(bookmark['tags'])}"
    else:
        text = f"{bookmark['title']}: {summary}"
    return text_result(
        text,
        {
            "bookmark": _public_bookmark(bookmark),
            "summary": summary,
            "style": style,
            "stub_notice": "Static fixture summary only; no network fetch, crawler, external API, or LLM was used.",
        },
    )


def bookmark_create(args: dict[str, Any]) -> dict[str, Any]:
    global _NEXT_BOOKMARK_NUMBER

    title, title_error = _required_text(args, "title", max_length=180)
    if title_error:
        return title_error
    url, url_error = _required_text(args, "url", max_length=2048)
    if url_error:
        return url_error
    normalized_url, normalized_url_error = _validate_url(url)
    if normalized_url_error:
        return normalized_url_error
    tags, tags_error = _normalize_tags(args.get("tags"))
    if tags_error:
        return tags_error
    summary, summary_error = _optional_text(args, "summary", default="", max_length=1000)
    if summary_error:
        return summary_error
    notes, notes_error = _optional_text(args, "notes", default="", max_length=1000)
    if notes_error:
        return notes_error

    bookmark_id = f"bookmark-{_NEXT_BOOKMARK_NUMBER:03d}"
    _NEXT_BOOKMARK_NUMBER += 1
    bookmark = {
        "id": bookmark_id,
        "title": title,
        "url": normalized_url,
        "tags": tags or [],
        "summary": summary or "Static user-provided bookmark fixture; no crawling performed.",
        "notes": notes or "",
        "captured_at": _utc_now(),
        "source": "user-created-demo",
    }
    _BOOKMARKS[bookmark_id] = bookmark
    _DELETED_BOOKMARK_IDS.discard(bookmark_id)
    return text_result(f"Created {_bookmark_line(bookmark)}", {"bookmark": _public_bookmark(bookmark), "created": True})


def bookmark_delete(args: dict[str, Any]) -> dict[str, Any]:
    bookmark_id, bookmark_id_error = _required_text(args, "bookmark_id", max_length=60)
    if bookmark_id_error:
        return bookmark_id_error
    reason, reason_error = _optional_text(args, "reason", max_length=240)
    if reason_error:
        return reason_error

    bookmark = _BOOKMARKS.pop(bookmark_id, None)
    if bookmark is None:
        if bookmark_id in _DELETED_BOOKMARK_IDS:
            return text_result(
                f"Bookmark '{bookmark_id}' was already deleted.",
                {"bookmark_id": bookmark_id, "deleted": True, "already_deleted": True},
            )
        return _error_result("not_found", f"Bookmark '{bookmark_id}' was not found", bookmark_id=bookmark_id)

    _DELETED_BOOKMARK_IDS.add(bookmark_id)
    return text_result(
        f"Deleted bookmark '{bookmark_id}'.",
        {
            "bookmark_id": bookmark_id,
            "deleted": True,
            "already_deleted": False,
            "reason": reason,
            "deleted_bookmark_snapshot": _public_bookmark(bookmark),
        },
    )


TOOLS = [
    tool(
        name="bookmark_search",
        title="Search bookmarks",
        description="Search deterministic in-memory bookmark fixtures by text and optional tags.",
        input_schema=object_schema(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 160},
                "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            required=["query"],
        ),
        read_only=True,
    ),
    tool(
        name="bookmark_list_by_tag",
        title="List bookmarks by tag",
        description="List active in-memory bookmarks that include a tag.",
        input_schema=object_schema(
            {
                "tag": {"type": "string", "minLength": 1, "maxLength": 80},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            },
            required=["tag"],
        ),
        read_only=True,
    ),
    tool(
        name="bookmark_summarize_stub",
        title="Summarize bookmark fixture",
        description="Return a static fixture summary for a saved bookmark without network, crawler, API, or LLM calls.",
        input_schema=object_schema(
            {
                "bookmark_id": {"type": "string"},
                "style": {"type": "string", "enum": ["short", "bullets"], "default": "short"},
            },
            required=["bookmark_id"],
        ),
        read_only=True,
    ),
    tool(
        name="bookmark_create",
        title="Create bookmark",
        description="Create an in-memory bookmark from caller-provided metadata; the server never fetches the URL.",
        input_schema=object_schema(
            {
                "title": {"type": "string", "minLength": 1, "maxLength": 180},
                "url": {"type": "string", "minLength": 1, "maxLength": 2048},
                "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
                "summary": {"type": "string", "maxLength": 1000},
                "notes": {"type": "string", "maxLength": 1000},
            },
            required=["title", "url"],
        ),
        read_only=False,
        idempotent=False,
    ),
    tool(
        name="bookmark_delete",
        title="Delete bookmark",
        description="Delete a bookmark from active in-memory results. This is a destructive demo mutation.",
        input_schema=object_schema(
            {
                "bookmark_id": {"type": "string"},
                "reason": {"type": "string", "maxLength": 240},
            },
            required=["bookmark_id"],
        ),
        read_only=False,
        destructive=True,
        idempotent=True,
    ),
]

HANDLERS = {
    "bookmark_search": bookmark_search,
    "bookmark_list_by_tag": bookmark_list_by_tag,
    "bookmark_summarize_stub": bookmark_summarize_stub,
    "bookmark_create": bookmark_create,
    "bookmark_delete": bookmark_delete,
}

_reset_state()

SERVER = DemoMcpServer(
    slug="bookmark-research",
    service_slug="demo_bookmarks",
    title="Bookmark Research MCP",
    description="가상의 북마크 리서치 MCP",
    tools=TOOLS,
    handlers=HANDLERS,
    reset=_reset_state,
)
