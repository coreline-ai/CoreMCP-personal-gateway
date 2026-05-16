from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI


@dataclass(slots=True)
class McpHandlerContext:
    """Small dependency bundle for MCP handlers.

    The object keeps handler code from reaching into ``app.state`` for every
    dependency while preserving the current FastAPI app lifecycle ownership.
    """

    app: FastAPI
    settings: Any
    repos: Any
    sessions: Any
    downstream: Any
    idempotency_cache: Any
    circuit_breaker: Any
    plugins: Any
    inflight_downstream_calls: dict[str, Any]

    @classmethod
    def from_app(cls, app: FastAPI) -> "McpHandlerContext":
        state = app.state
        return cls(
            app=app,
            settings=state.settings,
            repos=state.repos,
            sessions=state.sessions,
            downstream=state.downstream,
            idempotency_cache=state.idempotency_cache,
            circuit_breaker=state.circuit_breaker,
            plugins=state.plugins,
            inflight_downstream_calls=state.inflight_downstream_calls,
        )

    @property
    def tool_registry(self) -> dict[str, dict[str, Any]]:
        return getattr(self.app.state, "tool_registry", {})

    def set_tool_registry(self, registry: dict[str, dict[str, Any]]) -> None:
        self.app.state.tool_registry = registry
