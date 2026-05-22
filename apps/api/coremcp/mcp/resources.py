from __future__ import annotations

from typing import Any

RESOURCE_READ_MAX_TEXT_CHARS = 20_000
RESOURCE_READ_MAX_BLOB_CHARS = 1_000_000


def cached_resource_to_mcp(row: dict[str, Any]) -> dict[str, Any]:
    raw_metadata = row.get("metadata_json")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    item: dict[str, Any] = dict(metadata)
    item["uri"] = row["uri"]
    if row.get("name"):
        item["name"] = row["name"]
    if row.get("title"):
        item["title"] = row["title"]
    if row.get("description"):
        item["description"] = row["description"]
    if row.get("mime_type"):
        item["mimeType"] = row["mime_type"]
    if isinstance(row.get("annotations"), dict) and row["annotations"]:
        item["annotations"] = row["annotations"]
    return item


def unambiguous_resource_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        uri = str(row.get("uri") or "")
        if uri:
            counts[uri] = counts.get(uri, 0) + 1
    return [row for row in rows if counts.get(str(row.get("uri") or ""), 0) == 1]


def cached_resource_template_to_mcp(row: dict[str, Any]) -> dict[str, Any]:
    raw_metadata = row.get("metadata_json")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    item: dict[str, Any] = dict(metadata)
    item["uriTemplate"] = row["uri_template"]
    if row.get("name"):
        item["name"] = row["name"]
    if row.get("title"):
        item["title"] = row["title"]
    if row.get("description"):
        item["description"] = row["description"]
    if row.get("mime_type"):
        item["mimeType"] = row["mime_type"]
    if isinstance(row.get("annotations"), dict) and row["annotations"]:
        item["annotations"] = row["annotations"]
    return item


def resource_content_meta(*, kind: str, original_length: int, max_length: int) -> dict[str, Any]:
    return {
        "truncated": True,
        "kind": kind,
        "originalLength": original_length,
        "maxLength": max_length,
        "reason": "resource_content_too_large",
    }


def truncate_resource_read_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep `resources/read` responses usable for LLM clients."""

    contents = result.get("contents")
    if not isinstance(contents, list):
        return result

    truncated_any = False
    normalized_contents: list[Any] = []
    for item in contents:
        if not isinstance(item, dict):
            normalized_contents.append(item)
            continue

        normalized = dict(item)
        item_meta = dict(normalized.get("_meta") or {}) if isinstance(normalized.get("_meta"), dict) else {}

        text = normalized.get("text")
        if isinstance(text, str) and len(text) > RESOURCE_READ_MAX_TEXT_CHARS:
            normalized["text"] = text[:RESOURCE_READ_MAX_TEXT_CHARS] + "\n…[CoreMCP truncated oversized resource text]"
            item_meta["coremcp"] = resource_content_meta(
                kind="text",
                original_length=len(text),
                max_length=RESOURCE_READ_MAX_TEXT_CHARS,
            )
            truncated_any = True

        blob = normalized.get("blob")
        if isinstance(blob, str) and len(blob) > RESOURCE_READ_MAX_BLOB_CHARS:
            normalized["blob"] = blob[:RESOURCE_READ_MAX_BLOB_CHARS]
            item_meta["coremcp"] = resource_content_meta(
                kind="blob",
                original_length=len(blob),
                max_length=RESOURCE_READ_MAX_BLOB_CHARS,
            )
            truncated_any = True

        if item_meta:
            normalized["_meta"] = item_meta
        normalized_contents.append(normalized)

    if not truncated_any:
        return result

    normalized_result = dict(result)
    normalized_result["contents"] = normalized_contents
    result_meta = dict(normalized_result.get("_meta") or {}) if isinstance(normalized_result.get("_meta"), dict) else {}
    result_meta["coremcp"] = {
        "truncated": True,
        "reason": "resource_content_too_large",
        "maxTextChars": RESOURCE_READ_MAX_TEXT_CHARS,
        "maxBlobChars": RESOURCE_READ_MAX_BLOB_CHARS,
    }
    normalized_result["_meta"] = result_meta
    return normalized_result
