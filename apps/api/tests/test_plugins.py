from __future__ import annotations

from pathlib import Path
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
async def test_empty_plugin_registry_is_noop_boundary() -> None:
    registry = PluginRegistry()
    arguments = {"text": "hello"}
    result = {"content": [{"type": "text", "text": "ok"}]}

    assert registry.plugins == ()
    assert await registry.before_tool_call(_context(), arguments) is arguments
    assert await registry.after_tool_response(_context(), result) is result


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


def test_resources_and_prompts_plugin_hooks_remain_inactive_until_security_adr() -> None:
    api_root = Path(__file__).resolve().parents[1]
    handler_paths = [
        api_root / "coremcp" / "mcp" / "resources_handlers.py",
        api_root / "coremcp" / "mcp" / "prompts_handlers.py",
    ]

    for handler_path in handler_paths:
        source = handler_path.read_text()
        assert "coremcp.plugins" not in source
        assert "PluginExecutionError" not in source
        assert ".plugins." not in source
        assert "before_resource" not in source
        assert "after_resource" not in source
        assert "before_prompt" not in source
        assert "after_prompt" not in source
