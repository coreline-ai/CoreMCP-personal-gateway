"""MCP gateway helper modules.

This package holds small, behavior-preserving helpers extracted from
``coremcp.main`` so route handlers can stay focused on request flow.
"""

from __future__ import annotations

from .catalog import active_toolbox_services, toolbox_unavailable_services
from .dispatcher import McpDispatchHandlers, dispatch_mcp, dispatch_mcp_batch
from .prompts import cached_prompt_to_mcp
from .prompts_handlers import PromptsHandlerDeps, handle_prompts_get, handle_prompts_list
from .resources import (
    RESOURCE_READ_MAX_BLOB_CHARS,
    RESOURCE_READ_MAX_TEXT_CHARS,
    cached_resource_template_to_mcp,
    cached_resource_to_mcp,
    truncate_resource_read_result,
    unambiguous_resource_rows,
)
from .resources_handlers import ResourcesHandlerDeps, handle_resources_list, handle_resources_read
from .rpc import (
    RpcHelperDeps,
    request_default_downstream_rpc,
    request_service_rpc,
    service_transport_type,
)
from .tools_handlers import ToolsHandlerDeps, handle_tools_call, handle_tools_list, normalize_downstream_tool, refresh_tools

__all__ = [
    "McpDispatchHandlers",
    "PromptsHandlerDeps",
    "RESOURCE_READ_MAX_BLOB_CHARS",
    "RESOURCE_READ_MAX_TEXT_CHARS",
    "ResourcesHandlerDeps",
    "RpcHelperDeps",
    "ToolsHandlerDeps",
    "active_toolbox_services",
    "cached_prompt_to_mcp",
    "cached_resource_template_to_mcp",
    "cached_resource_to_mcp",
    "dispatch_mcp",
    "dispatch_mcp_batch",
    "handle_prompts_get",
    "handle_prompts_list",
    "handle_resources_list",
    "handle_resources_read",
    "handle_tools_call",
    "handle_tools_list",
    "normalize_downstream_tool",
    "refresh_tools",
    "request_default_downstream_rpc",
    "request_service_rpc",
    "service_transport_type",
    "toolbox_unavailable_services",
    "truncate_resource_read_result",
    "unambiguous_resource_rows",
]
