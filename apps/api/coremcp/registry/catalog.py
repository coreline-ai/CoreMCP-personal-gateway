from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from coremcp.settings import Settings

PROMPT_INJECTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"disregard\s+(the\s+)?system\s+prompt",
        r"reveal\s+(your\s+)?(system|developer)\s+prompt",
        r"jailbreak",
    ]
]


def canonical_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def schema_hash(value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def slugify_tool_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._-") or "tool"


def scan_tool_metadata(tool: dict[str, Any]) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    text = "\n".join(str(tool.get(key) or "") for key in ("name", "title", "description"))
    normalized = unicodedata.normalize("NFKC", text)
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern.search(normalized):
            warnings.append({"code": "prompt_injection_phrase", "message": pattern.pattern})
    if len(normalized) > 4000:
        warnings.append({"code": "long_description", "message": "Tool metadata is unusually long"})
    if any(unicodedata.category(ch) in {"Cf", "Cc"} and ch not in "\n\r\t" for ch in normalized):
        warnings.append({"code": "unicode_control", "message": "Tool metadata contains control characters"})
    if normalized != text:
        warnings.append({"code": "unicode_normalized", "message": "Tool metadata changes under NFKC normalization"})
    risk_level = "medium" if warnings else "low"
    return {"risk_level": risk_level, "warnings": warnings}


def normalize_icons(tool: dict[str, Any], settings: Settings) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    raw_icons = tool.get("icons") or []
    warnings: list[dict[str, str]] = []
    if not isinstance(raw_icons, list):
        return [], [{"code": "icons_invalid", "message": "icons must be a list"}]
    normalized: list[dict[str, Any]] = []
    for icon in raw_icons:
        if not isinstance(icon, dict):
            warnings.append({"code": "icon_invalid", "message": "icon must be an object"})
            continue
        src = icon.get("src")
        mime_type = icon.get("mimeType") or icon.get("mime_type")
        if not isinstance(src, str) or not src:
            warnings.append({"code": "icon_src_missing", "message": "icon.src is required"})
            continue
        is_remote_https = src.startswith("https://")
        if not (is_remote_https or src.startswith("data:image/")):
            warnings.append({"code": "icon_src_blocked", "message": "icon src must be https or data:image"})
            continue
        if is_remote_https and not settings.remote_tool_icons_enabled:
            warnings.append({"code": "icon_remote_blocked", "message": "remote HTTPS icons are disabled"})
            continue
        if not isinstance(mime_type, str):
            if src.startswith("data:image/png"):
                mime_type = "image/png"
            elif src.startswith("data:image/webp"):
                mime_type = "image/webp"
            elif src.startswith("data:image/svg+xml") or src.endswith(".svg"):
                mime_type = "image/svg+xml"
            else:
                mime_type = "image/png"
        if mime_type not in {"image/png", "image/webp", "image/svg+xml"}:
            warnings.append({"code": "icon_mime_blocked", "message": f"unsupported icon mimeType {mime_type}"})
            continue
        if mime_type == "image/svg+xml" and not settings.icon_svg_enabled:
            warnings.append({"code": "icon_svg_blocked", "message": "SVG icons are disabled"})
            continue
        item: dict[str, Any] = {"src": src, "mimeType": mime_type}
        if icon.get("sizes"):
            item["sizes"] = icon["sizes"]
        if len(canonical_json(item).encode("utf-8")) > 32 * 1024:
            warnings.append({"code": "icon_too_large", "message": "icon metadata exceeds 32KB"})
            continue
        normalized.append(item)
    if len(canonical_json(normalized).encode("utf-8")) > 32 * 1024:
        warnings.append({"code": "icons_too_large", "message": "icons metadata exceeds 32KB"})
        return [], warnings
    return normalized, warnings


def normalize_downstream_tools(tools: list[Any], *, service_slug: str, settings: Settings) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    normalized: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    for raw in tools:
        if not isinstance(raw, dict) or not raw.get("name"):
            warnings.append({"code": "tool_invalid", "message": "tool item missing name"})
            continue
        original_name = str(raw["name"]).strip()
        safe_name = slugify_tool_name(original_name)
        exposed_name = f"{service_slug}.{safe_name}"
        input_schema = raw.get("inputSchema") if isinstance(raw.get("inputSchema"), dict) else {"type": "object"}
        scan = scan_tool_metadata(raw)
        icons, icon_warnings = normalize_icons(raw, settings)
        all_warnings = [*scan["warnings"], *icon_warnings]
        warnings.extend(all_warnings)
        normalized.append(
            {
                "original_name": original_name,
                "exposed_name": exposed_name,
                "title": raw.get("title"),
                "description": raw.get("description"),
                "input_schema_json": input_schema,
                "output_schema_json": raw.get("outputSchema"),
                "structured_output_schema_json": raw.get("structuredOutputSchema"),
                "annotations": raw.get("annotations") if isinstance(raw.get("annotations"), dict) else {},
                "icons_json": icons,
                "schema_hash": schema_hash(input_schema),
                "risk_level": scan["risk_level"],
                "metadata_scan": {"warnings": all_warnings},
            }
        )
    return normalized, warnings


def catalog_row_to_mcp_tool(row: dict[str, Any]) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "name": row["exposed_name"],
        "inputSchema": row.get("input_schema_json") or {"type": "object"},
        "annotations": row.get("annotations") or {},
    }
    if row.get("title"):
        tool["title"] = row["title"]
    if row.get("description"):
        tool["description"] = row["description"]
    if row.get("icons_json"):
        tool["icons"] = row["icons_json"]
    if row.get("output_schema_json"):
        tool["outputSchema"] = row["output_schema_json"]
    if row.get("structured_output_schema_json"):
        tool["structuredOutputSchema"] = row["structured_output_schema_json"]
    return tool
