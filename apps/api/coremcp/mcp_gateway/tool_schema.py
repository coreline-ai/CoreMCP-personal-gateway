"""Tool catalog schema-diff helpers.

Extracted from ``coremcp.main`` per ADR-042. Pure functions: compare an
existing tool catalog row set with the normalised downstream ``tools/list``
output and return the change summary used by service validation and health
drift detection.
"""

from __future__ import annotations

from typing import Any


def tool_schema_diff(
    existing_tools: list[dict[str, Any]],
    normalized_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    existing_by_name = {str(tool.get("original_name")): tool for tool in existing_tools}
    normalized_by_name = {str(tool.get("original_name")): tool for tool in normalized_tools}
    added_names = sorted(set(normalized_by_name) - set(existing_by_name))
    removed_names = sorted(set(existing_by_name) - set(normalized_by_name))
    changed_tools: list[dict[str, Any]] = []
    for name in sorted(set(existing_by_name) & set(normalized_by_name)):
        previous_hash = existing_by_name.get(name, {}).get("schema_hash")
        current_hash = normalized_by_name.get(name, {}).get("schema_hash")
        if previous_hash != current_hash:
            changed_tools.append(
                {
                    "name": name,
                    "previous_schema_hash": previous_hash,
                    "current_schema_hash": current_hash,
                }
            )
    summary = {
        "previous_tool_count": len(existing_tools),
        "discovered_tool_count": len(normalized_tools),
        "changed_tool_count": len(changed_tools) + len(added_names) + len(removed_names),
        "added_tool_count": len(added_names),
        "removed_tool_count": len(removed_names),
    }
    details = {
        "added": [
            {
                "name": name,
                "schema_hash": normalized_by_name.get(name, {}).get("schema_hash"),
            }
            for name in added_names
        ],
        "removed": [
            {
                "name": name,
                "schema_hash": existing_by_name.get(name, {}).get("schema_hash"),
            }
            for name in removed_names
        ],
        "changed": changed_tools,
    }
    return {"summary": summary, "details": details}


def tool_schema_change_summary(
    existing_tools: list[dict[str, Any]],
    normalized_tools: list[dict[str, Any]],
) -> dict[str, int]:
    return tool_schema_diff(existing_tools, normalized_tools)["summary"]


__all__ = ["tool_schema_change_summary", "tool_schema_diff"]
