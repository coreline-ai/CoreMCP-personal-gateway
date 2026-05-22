"""Pydantic response models shared across CoreMCP API routes.

Schemas live here so they can be referenced by ``response_model=`` on route
handlers and surface in the auto-generated OpenAPI document. New schemas
should match the existing response shape exactly — Phase 4 of
dev-plan/implement_20260522_183112.md tightens types without changing the
wire contract.
"""

from __future__ import annotations

from typing import Any

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


__all__ = [
    "ToolboxDetail",
    "ToolboxItemSummary",
    "ToolboxList",
    "ToolboxSummary",
]
