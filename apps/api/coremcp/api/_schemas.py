"""Pydantic response models shared across CoreMCP API routes.

Schemas live here so they can be referenced by ``response_model=`` on route
handlers and surface in the auto-generated OpenAPI document. New schemas
should match the existing response shape exactly — Phase 4 of
dev-plan/implement_20260522_183112.md tightens types without changing the
wire contract.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ToolboxSummary(BaseModel):
    """Default toolbox or named alternate, as returned by ``list_toolboxes``."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    is_default: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class ToolboxList(BaseModel):
    items: list[ToolboxSummary]
    next_cursor: str | None = None


class ToolboxItemSummary(BaseModel):
    """One toolbox membership row pointing at an MCP service."""

    model_config = ConfigDict(extra="allow")

    id: str
    toolbox_id: str
    service_id: str
    enabled: bool
    created_at: str | None = None
    updated_at: str | None = None


class ToolboxDetail(ToolboxSummary):
    items: list[ToolboxItemSummary] = Field(default_factory=list)


class ServiceSummary(BaseModel):
    """One row of GET /v1/mcp-services list."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    slug: str
    status: str
    endpoint_url: str | None = None
    description: str | None = None
    category: str | None = None
    logo_url: str | None = None
    homepage_url: str | None = None
    documentation_url: str | None = None
    auth_type: str | None = None
    tool_count: int | None = None
    risk_level: str | None = None
    credential_status: str | None = None
    credential_masked: str | None = None
    last_validated_at: str | None = None
    updated_at: str | None = None


class ServiceList(BaseModel):
    items: list[ServiceSummary]
    next_cursor: str | None = None


class ServiceCredentialMasked(BaseModel):
    """Response shape from PUT/POST /v1/mcp-services/{id}/credential and rotate."""

    model_config = ConfigDict(extra="allow")

    status: str
    masked: str | None = None
    updated_at: str | None = None


class ServiceToolSummary(BaseModel):
    """One row of GET /v1/mcp-services/{id}/tools."""

    model_config = ConfigDict(extra="allow")

    id: str
    service_id: str
    original_name: str
    exposed_name: str | None = None
    title: str | None = None
    description: str | None = None
    status: str | None = None
    risk_level: str | None = None
    schema_hash: str | None = None
    validation_status: str | None = None


class ServiceToolList(BaseModel):
    items: list[ServiceToolSummary]
    next_cursor: str | None = None


class ToolOverrideSummary(BaseModel):
    """One row of GET /v1/mcp-services/{id}/tool-overrides."""

    model_config = ConfigDict(extra="allow")

    id: str
    toolbox_id: str
    service_id: str
    service_tool_id: str
    exposed_name: str
    enabled: bool
    permission_level: str
    updated_at: str | None = None


class ToolOverrideList(BaseModel):
    items: list[ToolOverrideSummary]
    next_cursor: str | None = None


class ToolPresetResponse(BaseModel):
    """Response from POST /v1/mcp-services/{id}/tool-overrides/preset."""

    model_config = ConfigDict(extra="allow")

    preset: str
    items: list[ToolOverrideSummary]
    counts: dict[str, int]
    next_cursor: str | None = None


class PlaygroundToolSummary(BaseModel):
    """One row of GET /v1/playground/tools/list."""

    model_config = ConfigDict(extra="allow")

    name: str
    title: str | None = None
    description: str | None = None


class PlaygroundToolList(BaseModel):
    items: list[PlaygroundToolSummary]
    next_cursor: str | None = None


class ExternalConnectionSummary(BaseModel):
    """One row of GET /v1/external-connections."""

    model_config = ConfigDict(extra="allow")

    id: str
    client_type: str
    client_name: str
    status: str
    last_used_at: str | None = None
    created_at: str | None = None


class ExternalConnectionList(BaseModel):
    items: list[ExternalConnectionSummary]
    next_cursor: str | None = None


class ClientTokenSummary(BaseModel):
    """One row of GET /v1/settings/client-tokens."""

    model_config = ConfigDict(extra="allow")

    id: str
    external_connection_id: str
    token_prefix: str
    scopes: list[str]
    status: str
    last_used_at: str | None = None
    created_at: str | None = None
    revoked_at: str | None = None


class ClientTokenList(BaseModel):
    items: list[ClientTokenSummary]
    next_cursor: str | None = None


class CodexSimulatorToolCall(BaseModel):
    model_config = ConfigDict(extra="allow")

    server: str | None = None
    name: str
    status: str | None = None


class CodexSimulatorResponse(BaseModel):
    """Response from POST /v1/simulator/codex/run."""

    model_config = ConfigDict(extra="allow")

    request_id: str | None = None
    status: str | None = None


__all__ = [
    "ClientTokenList",
    "ClientTokenSummary",
    "CodexSimulatorResponse",
    "CodexSimulatorToolCall",
    "ExternalConnectionList",
    "ExternalConnectionSummary",
    "PlaygroundToolList",
    "PlaygroundToolSummary",
    "ServiceCredentialMasked",
    "ServiceList",
    "ServiceSummary",
    "ServiceToolList",
    "ServiceToolSummary",
    "ToolOverrideList",
    "ToolOverrideSummary",
    "ToolPresetResponse",
    "ToolboxDetail",
    "ToolboxItemSummary",
    "ToolboxList",
    "ToolboxSummary",
]
