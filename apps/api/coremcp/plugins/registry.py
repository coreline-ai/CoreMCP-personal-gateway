from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from coremcp.errors import CoreMcpRuntimeError

from .base import ToolCallContext, ToolCallPlugin


class PluginExecutionError(CoreMcpRuntimeError):
    """Raised when an in-process plugin hook fails."""

    def __init__(self, *, plugin_name: str, stage: str, cause: Exception) -> None:
        super().__init__(f"plugin '{plugin_name}' failed during {stage}: {cause}")
        self.plugin_name = plugin_name
        self.stage = stage
        self.cause = cause


class PluginRegistry:
    """Ordered, in-process plugin registry.

    CoreMCP keeps this registry empty by default. That makes route/dispatcher
    stabilization behavior-preserving while creating a narrow, testable hook
    boundary for future built-ins such as redaction or deny-list plugins.
    """

    def __init__(self, plugins: Iterable[ToolCallPlugin] | None = None) -> None:
        self._plugins: list[ToolCallPlugin] = list(plugins or [])

    @property
    def plugins(self) -> tuple[ToolCallPlugin, ...]:
        return tuple(self._plugins)

    def register(self, plugin: ToolCallPlugin) -> None:
        if any(existing.name == plugin.name for existing in self._plugins):
            raise ValueError(f"plugin already registered: {plugin.name}")
        self._plugins.append(plugin)

    async def before_tool_call(self, context: ToolCallContext, arguments: Any) -> Any:
        value = arguments
        for plugin in self._plugins:
            try:
                value = await plugin.before_tool_call(context, value)
            except Exception as exc:  # noqa: BLE001 - plugin boundary must normalize failures.
                raise PluginExecutionError(plugin_name=plugin.name, stage="before_tool_call", cause=exc) from exc
        return value

    async def after_tool_response(self, context: ToolCallContext, result: dict[str, Any]) -> dict[str, Any]:
        value = result
        for plugin in reversed(self._plugins):
            try:
                value = await plugin.after_tool_response(context, value)
            except Exception as exc:  # noqa: BLE001 - plugin boundary must normalize failures.
                raise PluginExecutionError(plugin_name=plugin.name, stage="after_tool_response", cause=exc) from exc
        return value
