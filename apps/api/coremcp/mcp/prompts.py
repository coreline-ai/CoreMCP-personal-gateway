from __future__ import annotations

from typing import Any


def cached_prompt_to_mcp(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    item = dict(metadata)
    item["name"] = f"{row['service_slug']}.{row['name']}"
    if row.get("title"):
        item["title"] = row["title"]
    if row.get("description"):
        item["description"] = row["description"]
    if isinstance(row.get("arguments_json"), list):
        item["arguments"] = row["arguments_json"]
    return item
