from __future__ import annotations

from copy import deepcopy
from typing import Any

from demo_mcp_suite.runtime import DemoMcpServer, object_schema, text_result, tool

_DEMO_UPDATED_AT = "2026-05-14T00:00:00Z"

_INITIAL_ASSETS: list[dict[str, Any]] = [
    {
        "asset_id": "asset_button_primary",
        "name": "Primary Button",
        "type": "component",
        "status": "active",
        "description": "Primary CTA button for high-emphasis actions.",
        "tags": ["button", "cta", "core"],
        "component_id": "component_button_primary",
        "owner": "design-system",
        "updated_at": "2026-05-01T09:00:00Z",
    },
    {
        "asset_id": "asset_modal_sheet",
        "name": "Modal Sheet",
        "type": "component",
        "status": "active",
        "description": "Responsive modal sheet with action footer and focus trapping guidance.",
        "tags": ["modal", "dialog", "layout"],
        "component_id": "component_modal_sheet",
        "owner": "design-system",
        "updated_at": "2026-05-04T15:30:00Z",
    },
    {
        "asset_id": "asset_icon_spark",
        "name": "Spark Icon",
        "type": "icon",
        "status": "active",
        "description": "Small sparkle icon used for generated suggestions and premium affordances.",
        "tags": ["icon", "spark", "ai"],
        "owner": "brand",
        "updated_at": "2026-04-28T12:00:00Z",
    },
    {
        "asset_id": "asset_legacy_card",
        "name": "Legacy Marketing Card",
        "type": "component",
        "status": "deprecated",
        "description": "Older promo card retained for migration demos.",
        "tags": ["card", "marketing", "legacy"],
        "component_id": "component_legacy_card",
        "owner": "marketing",
        "updated_at": "2026-03-10T10:00:00Z",
        "deprecated_reason": "Replaced by Surface Card v2.",
        "deprecated_at": "2026-04-01T00:00:00Z",
    },
]

_INITIAL_COMPONENTS: dict[str, dict[str, Any]] = {
    "component_button_primary": {
        "component_id": "component_button_primary",
        "name": "Primary Button",
        "status": "active",
        "description": "High-emphasis action button for forms, dialogs, and hero CTAs.",
        "variants": [
            {"name": "default", "state": "enabled"},
            {"name": "hover", "state": "interactive"},
            {"name": "disabled", "state": "disabled"},
        ],
        "props": {
            "label": {"type": "string", "required": True},
            "size": {"type": "enum", "values": ["sm", "md", "lg"], "default": "md"},
            "leadingIcon": {"type": "icon", "required": False},
        },
        "tokens": {
            "background": "color.action.primary",
            "foreground": "color.text.inverse",
            "radius": "radius.md",
            "spacingX": "space.4",
        },
        "accessibility": {
            "role": "button",
            "keyboard": ["Enter", "Space"],
            "notes": "Use an explicit accessible label when the visible label is abbreviated.",
        },
    },
    "component_modal_sheet": {
        "component_id": "component_modal_sheet",
        "name": "Modal Sheet",
        "status": "active",
        "description": "Layered sheet for focused decisions and short workflows.",
        "variants": [
            {"name": "center", "state": "desktop"},
            {"name": "bottom", "state": "mobile"},
        ],
        "props": {
            "title": {"type": "string", "required": True},
            "dismissible": {"type": "boolean", "default": True},
            "primaryAction": {"type": "object", "required": False},
        },
        "tokens": {
            "surface": "color.surface.overlay",
            "scrim": "color.overlay.scrim",
            "radius": "radius.xl",
            "shadow": "shadow.dialog",
        },
        "accessibility": {
            "role": "dialog",
            "keyboard": ["Escape"],
            "notes": "Trap focus while open and restore focus to the launcher on close.",
        },
    },
    "component_legacy_card": {
        "component_id": "component_legacy_card",
        "name": "Legacy Marketing Card",
        "status": "deprecated",
        "description": "Deprecated promo card kept to demonstrate destructive tool filtering.",
        "variants": [{"name": "default", "state": "legacy"}],
        "props": {
            "headline": {"type": "string", "required": True},
            "image": {"type": "asset", "required": False},
        },
        "tokens": {
            "surface": "color.surface.legacy",
            "radius": "radius.sm",
        },
        "accessibility": {
            "role": "group",
            "notes": "Do not use for new production surfaces.",
        },
    },
}

_COLOR_TOKENS: list[dict[str, str]] = [
    {
        "name": "color.action.primary",
        "theme": "light",
        "value": "#2557D6",
        "family": "semantic",
        "usage": "Primary call-to-action backgrounds.",
        "contrast_on": "#FFFFFF",
    },
    {
        "name": "color.action.primary",
        "theme": "dark",
        "value": "#87A7FF",
        "family": "semantic",
        "usage": "Primary call-to-action backgrounds.",
        "contrast_on": "#0B1020",
    },
    {
        "name": "color.text.primary",
        "theme": "light",
        "value": "#111827",
        "family": "semantic",
        "usage": "Default body and heading text.",
        "contrast_on": "#FFFFFF",
    },
    {
        "name": "color.text.primary",
        "theme": "dark",
        "value": "#F9FAFB",
        "family": "semantic",
        "usage": "Default body and heading text.",
        "contrast_on": "#111827",
    },
    {
        "name": "color.surface.canvas",
        "theme": "light",
        "value": "#F8FAFC",
        "family": "core",
        "usage": "App-level canvas background.",
        "contrast_on": "#111827",
    },
    {
        "name": "color.surface.canvas",
        "theme": "dark",
        "value": "#0B1020",
        "family": "core",
        "usage": "App-level canvas background.",
        "contrast_on": "#F9FAFB",
    },
    {
        "name": "color.feedback.warning",
        "theme": "all",
        "value": "#F59E0B",
        "family": "semantic",
        "usage": "Warnings and recoverable risk states.",
        "contrast_on": "#111827",
    },
]

_assets: list[dict[str, Any]] = []
_components: dict[str, dict[str, Any]] = {}
_next_asset_seq = 1


def _reset_state() -> None:
    global _assets, _components, _next_asset_seq
    _assets = deepcopy(_INITIAL_ASSETS)
    _components = deepcopy(_INITIAL_COMPONENTS)
    _next_asset_seq = 1


def _error_result(message: str, *, code: str = "invalid_arguments", details: dict[str, Any] | None = None) -> dict[str, Any]:
    structured: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        structured["error"]["details"] = details
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": structured,
        "isError": True,
    }


def _string_arg(args: dict[str, Any], name: str, default: str = "") -> str:
    value = args.get(name, default)
    if value is None:
        return default
    return str(value).strip()


def _bool_arg(args: dict[str, Any], name: str, default: bool = False) -> bool:
    value = args.get(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
    return default


def _limit_arg(args: dict[str, Any], default: int = 10, maximum: int = 50) -> int:
    value = args.get("limit", default)
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, maximum))


def _string_list_arg(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        normalized = str(item).strip()
        if normalized:
            items.append(normalized)
    return items


def _asset_summary(asset: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "asset_id",
        "name",
        "type",
        "status",
        "description",
        "tags",
        "component_id",
        "owner",
        "updated_at",
        "deprecated_reason",
        "deprecated_at",
    ]
    return {key: deepcopy(asset[key]) for key in keys if key in asset}


def _find_asset(asset_id: str) -> dict[str, Any] | None:
    return next((asset for asset in _assets if asset["asset_id"] == asset_id), None)


def _next_asset_id() -> str:
    global _next_asset_seq
    while True:
        asset_id = f"asset_demo_{_next_asset_seq:03d}"
        _next_asset_seq += 1
        if _find_asset(asset_id) is None:
            return asset_id


def _search_assets(args: dict[str, Any]) -> dict[str, Any]:
    query = _string_arg(args, "query").lower()
    asset_type = _string_arg(args, "type").lower()
    include_deprecated = _bool_arg(args, "include_deprecated", False)
    limit = _limit_arg(args)

    matches = []
    for asset in _assets:
        if not include_deprecated and asset.get("status") == "deprecated":
            continue
        if asset_type and asset.get("type") != asset_type:
            continue
        haystack = " ".join(
            [
                str(asset.get("asset_id", "")),
                str(asset.get("name", "")),
                str(asset.get("description", "")),
                " ".join(asset.get("tags", [])),
            ]
        ).lower()
        if query and query not in haystack:
            continue
        matches.append(_asset_summary(asset))

    matches = sorted(matches, key=lambda item: (item["status"] == "deprecated", item["name"].lower()))[:limit]
    return text_result(
        f"Found {len(matches)} design asset(s).",
        {
            "query": query,
            "type": asset_type or None,
            "include_deprecated": include_deprecated,
            "count": len(matches),
            "items": matches,
        },
    )


def _color_tokens(args: dict[str, Any]) -> dict[str, Any]:
    theme = _string_arg(args, "theme", "all").lower()
    family = _string_arg(args, "family").lower()
    limit = _limit_arg(args, default=50, maximum=100)
    if theme not in {"all", "light", "dark"}:
        return _error_result("theme must be one of: all, light, dark")

    items = []
    for token_item in _COLOR_TOKENS:
        token_theme = token_item["theme"]
        if theme != "all" and token_theme not in {theme, "all"}:
            continue
        if family and token_item["family"] != family:
            continue
        items.append(deepcopy(token_item))

    items = sorted(items, key=lambda item: (item["name"], item["theme"]))[:limit]
    return text_result(
        f"Returned {len(items)} color token(s).",
        {
            "theme": theme,
            "family": family or None,
            "count": len(items),
            "items": items,
        },
    )


def _component_get(args: dict[str, Any]) -> dict[str, Any]:
    component_id = _string_arg(args, "component_id")
    if not component_id:
        return _error_result("component_id is required")
    component = _components.get(component_id)
    if component is None:
        return _error_result(
            f"Component not found: {component_id}",
            code="not_found",
            details={"component_id": component_id},
        )
    return text_result(
        f"Loaded component spec for {component['name']}.",
        {"component": deepcopy(component)},
    )


def _asset_register(args: dict[str, Any]) -> dict[str, Any]:
    name = _string_arg(args, "name")
    asset_type = _string_arg(args, "type").lower()
    if not name:
        return _error_result("name is required")
    if asset_type not in {"component", "icon", "illustration", "template"}:
        return _error_result("type must be one of: component, icon, illustration, template")

    tags = _string_list_arg(args.get("tags"))
    description = _string_arg(args, "description", f"Demo {asset_type} asset registered through MCP.")
    owner = _string_arg(args, "owner", "demo-user")
    asset_id = _next_asset_id()

    asset: dict[str, Any] = {
        "asset_id": asset_id,
        "name": name,
        "type": asset_type,
        "status": "active",
        "description": description,
        "tags": tags,
        "owner": owner,
        "updated_at": _DEMO_UPDATED_AT,
    }

    if asset_type == "component":
        component_id = asset_id.replace("asset_", "component_", 1)
        component_spec = args.get("component_spec")
        if not isinstance(component_spec, dict):
            component_spec = {}
        component = {
            "component_id": component_id,
            "name": name,
            "status": "active",
            "description": description,
            "variants": deepcopy(component_spec.get("variants", [{"name": "default", "state": "enabled"}])),
            "props": deepcopy(component_spec.get("props", {})),
            "tokens": deepcopy(component_spec.get("tokens", {})),
            "accessibility": deepcopy(
                component_spec.get(
                    "accessibility",
                    {"role": "group", "notes": "Demo-registered component; review before production use."},
                )
            ),
        }
        _components[component_id] = component
        asset["component_id"] = component_id

    _assets.append(asset)
    return text_result(
        f"Registered design asset {asset_id}.",
        {
            "asset": _asset_summary(asset),
            "asset_count": len(_assets),
        },
    )


def _asset_deprecate(args: dict[str, Any]) -> dict[str, Any]:
    asset_id = _string_arg(args, "asset_id")
    reason = _string_arg(args, "reason", "Deprecated from MCP demo.")
    if not asset_id:
        return _error_result("asset_id is required")
    asset = _find_asset(asset_id)
    if asset is None:
        return _error_result(
            f"Asset not found: {asset_id}",
            code="not_found",
            details={"asset_id": asset_id},
        )

    asset["status"] = "deprecated"
    asset["deprecated_reason"] = reason
    asset["deprecated_at"] = _DEMO_UPDATED_AT
    asset["updated_at"] = _DEMO_UPDATED_AT

    component_id = asset.get("component_id")
    if isinstance(component_id, str) and component_id in _components:
        _components[component_id]["status"] = "deprecated"
        _components[component_id]["deprecated_reason"] = reason

    return text_result(
        f"Deprecated design asset {asset_id}.",
        {"asset": _asset_summary(asset)},
    )


_TOOLS = [
    tool(
        name="asset_search",
        title="Search design assets",
        description="Search the fixture-backed design asset catalog by query, type, and status.",
        input_schema=object_schema(
            {
                "query": {"type": "string", "description": "Case-insensitive search text."},
                "type": {
                    "type": "string",
                    "enum": ["component", "icon", "illustration", "template"],
                    "description": "Optional asset type filter.",
                },
                "include_deprecated": {
                    "type": "boolean",
                    "description": "Include deprecated assets in results.",
                    "default": False,
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            }
        ),
        read_only=True,
    ),
    tool(
        name="color_tokens",
        title="List color tokens",
        description="Return demo design-system color tokens for light, dark, or all themes.",
        input_schema=object_schema(
            {
                "theme": {"type": "string", "enum": ["all", "light", "dark"], "default": "all"},
                "family": {"type": "string", "description": "Optional family filter such as core or semantic."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
            }
        ),
        read_only=True,
    ),
    tool(
        name="component_get",
        title="Get component spec",
        description="Fetch a fixture-backed component specification by component_id.",
        input_schema=object_schema(
            {
                "component_id": {"type": "string", "description": "Component identifier returned by asset_search."},
            },
            required=["component_id"],
        ),
        read_only=True,
    ),
    tool(
        name="asset_register",
        title="Register design asset",
        description="Register a new in-memory design asset for the demo catalog.",
        input_schema=object_schema(
            {
                "name": {"type": "string", "description": "Human-readable asset name."},
                "type": {
                    "type": "string",
                    "enum": ["component", "icon", "illustration", "template"],
                    "description": "Asset type.",
                },
                "description": {"type": "string", "description": "Short asset description."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Search tags."},
                "owner": {"type": "string", "description": "Demo owner label."},
                "component_spec": {
                    "type": "object",
                    "description": "Optional component spec when type is component.",
                    "additionalProperties": True,
                },
            },
            required=["name", "type"],
        ),
        read_only=False,
        idempotent=False,
    ),
    tool(
        name="asset_deprecate",
        title="Deprecate design asset",
        description="Mark an asset as deprecated in the in-memory demo catalog.",
        input_schema=object_schema(
            {
                "asset_id": {"type": "string", "description": "Asset identifier to deprecate."},
                "reason": {"type": "string", "description": "Human-readable deprecation reason."},
            },
            required=["asset_id"],
        ),
        read_only=False,
        destructive=True,
    ),
]


SERVER = DemoMcpServer(
    slug="design-assets",
    service_slug="demo_design",
    title="Design Asset Catalog MCP",
    description="가상의 디자인 asset catalog MCP",
    tools=_TOOLS,
    handlers={
        "asset_search": _search_assets,
        "color_tokens": _color_tokens,
        "component_get": _component_get,
        "asset_register": _asset_register,
        "asset_deprecate": _asset_deprecate,
    },
    reset=_reset_state,
)

_reset_state()
