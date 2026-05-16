from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ToolCallContext:
    """Stable context exposed to in-process CoreMCP plugins.

    The framework is intentionally in-process and closed by default. External
    plugin loading is a separate security decision, not part of this module.
    """

    request_id: str
    session_id: str | None
    service_id: str | None
    service_tool_id: str | None
    exposed_name: str
    downstream_name: str | None
    auth_kind: str | None


class ToolCallPlugin(Protocol):
    """Minimal async hook contract for tool call stabilization plugins."""

    name: str

    async def before_tool_call(self, context: ToolCallContext, arguments: Any) -> Any:
        ...

    async def after_tool_response(self, context: ToolCallContext, result: dict[str, Any]) -> dict[str, Any]:
        ...
