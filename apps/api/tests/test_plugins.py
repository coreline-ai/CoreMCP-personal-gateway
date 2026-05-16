from __future__ import annotations

from typing import Any

import pytest

from coremcp.plugins import PluginExecutionError, PluginRegistry, ToolCallContext


class _RecorderPlugin:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self._events = events

    async def before_tool_call(self, context: ToolCallContext, arguments: Any) -> Any:
        self._events.append(f"before:{self.name}:{context.exposed_name}")
        updated = dict(arguments)
        updated[self.name] = True
        return updated

    async def after_tool_response(self, context: ToolCallContext, result: dict[str, Any]) -> dict[str, Any]:
        self._events.append(f"after:{self.name}:{context.exposed_name}")
        updated = dict(result)
        updated.setdefault("_plugins", []).append(self.name)
        return updated


class _FailingPlugin:
    name = "failing"

    async def before_tool_call(self, context: ToolCallContext, arguments: Any) -> Any:
        raise RuntimeError("before failed")

    async def after_tool_response(self, context: ToolCallContext, result: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("after failed")


def _context() -> ToolCallContext:
    return ToolCallContext(
        request_id="req_test",
        session_id="sess_test",
        service_id="svc_test",
        service_tool_id="tool_test",
        exposed_name="svc.echo",
        downstream_name="echo",
        auth_kind="admin",
    )


@pytest.mark.asyncio
async def test_plugin_registry_runs_before_in_order_and_after_in_reverse() -> None:
    events: list[str] = []
    registry = PluginRegistry([_RecorderPlugin("a", events), _RecorderPlugin("b", events)])

    arguments = await registry.before_tool_call(_context(), {"text": "hello"})
    result = await registry.after_tool_response(_context(), {"content": []})

    assert arguments == {"text": "hello", "a": True, "b": True}
    assert result["_plugins"] == ["b", "a"]
    assert events == ["before:a:svc.echo", "before:b:svc.echo", "after:b:svc.echo", "after:a:svc.echo"]


def test_plugin_registry_rejects_duplicate_names() -> None:
    registry = PluginRegistry()
    registry.register(_RecorderPlugin("redact", []))
    with pytest.raises(ValueError, match="redact"):
        registry.register(_RecorderPlugin("redact", []))


@pytest.mark.asyncio
async def test_plugin_registry_wraps_before_hook_failures() -> None:
    registry = PluginRegistry([_FailingPlugin()])

    with pytest.raises(PluginExecutionError) as raised:
        await registry.before_tool_call(_context(), {"text": "hello"})

    assert raised.value.plugin_name == "failing"
    assert raised.value.stage == "before_tool_call"
    assert isinstance(raised.value.cause, RuntimeError)


@pytest.mark.asyncio
async def test_plugin_registry_wraps_after_hook_failures() -> None:
    registry = PluginRegistry([_FailingPlugin()])

    with pytest.raises(PluginExecutionError) as raised:
        await registry.after_tool_response(_context(), {"content": []})

    assert raised.value.plugin_name == "failing"
    assert raised.value.stage == "after_tool_response"
    assert isinstance(raised.value.cause, RuntimeError)
