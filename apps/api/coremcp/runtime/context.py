# pyright: reportAttributeAccessIssue=false
# ``app.state`` is a starlette ``State`` object that uses ``__getattr__`` for
# arbitrary attribute access; pyright cannot statically prove the attributes
# exist on the dynamic State instance.
"""Typed read-only facade over ``FastAPI.state``.

``AppContext`` exists to decouple modules under ``coremcp.mcp_gateway.*`` /
``coremcp.runtime.*`` from the dynamic ``request.app.state`` blob populated by
``create_app``. Every attribute is a pass-through to the same underlying
``app.state`` slot, so the runtime behaviour is identical — the value is in
the type annotation: a single import gives the caller the full surface of
what state is available.

Keep this layer thin. No caching, no lazy creation, no validation. If a slot
is missing on ``app.state`` (e.g., a test factory skipped initialisation) the
underlying ``AttributeError`` propagates as it always did.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI, Request

    from coremcp.auth.rate_limit import FixedWindowRateLimiter
    from coremcp.credentials.vault import CredentialVault
    from coremcp.db.repository import Repository
    from coremcp.db.repository_facade import RepositoryFacades
    from coremcp.mcp_gateway import (
        IdempotencyCache,
        ListChangedEventBus,
        SessionStore,
    )
    from coremcp.plugins.registry import PluginRegistry
    from coremcp.proxy.circuit_breaker import CircuitBreaker
    from coremcp.proxy.downstream import DownstreamMcpClient
    from coremcp.proxy.stdio import StdioMcpClient
    from coremcp.settings import Settings


@dataclass(frozen=True, slots=True)
class AppContext:
    """Pass-through view of ``app.state`` slots populated by ``create_app``.

    Use :meth:`from_app` inside lifespan / startup code and
    :meth:`from_request` inside request handlers. All attribute accesses are
    direct reads from ``app.state``; mutating them goes through
    ``app.state.<name> = value`` as before.
    """

    app: FastAPI

    @classmethod
    def from_app(cls, app: FastAPI) -> AppContext:
        return cls(app=app)

    @classmethod
    def from_request(cls, request: Request) -> AppContext:
        return cls(app=request.app)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    @property
    def settings(self) -> Settings:
        return self.app.state.settings

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @property
    def repository(self) -> Repository:
        return self.app.state.repository

    @property
    def repos(self) -> RepositoryFacades:
        return self.app.state.repos

    @property
    def vault(self) -> CredentialVault:
        return self.app.state.vault

    # ------------------------------------------------------------------
    # HTTP / downstream MCP
    # ------------------------------------------------------------------
    @property
    def http_client(self) -> httpx.AsyncClient:
        return self.app.state.http_client

    @property
    def downstream(self) -> DownstreamMcpClient:
        return self.app.state.downstream

    @property
    def downstream_sessions(self) -> dict[str, Any]:
        return self.app.state.downstream_sessions

    @property
    def inflight_downstream_calls(self) -> dict[str, Any]:
        return self.app.state.inflight_downstream_calls

    # ------------------------------------------------------------------
    # STDIO process pool
    # ------------------------------------------------------------------
    @property
    def stdio_clients(self) -> dict[str, tuple[tuple[Any, ...], StdioMcpClient]]:
        return self.app.state.stdio_clients

    @property
    def stdio_clients_lock(self) -> asyncio.Lock:
        return self.app.state.stdio_clients_lock

    # ------------------------------------------------------------------
    # MCP gateway state
    # ------------------------------------------------------------------
    @property
    def sessions(self) -> SessionStore:
        return self.app.state.sessions

    @property
    def list_changed_bus(self) -> ListChangedEventBus:
        return self.app.state.list_changed_bus

    @property
    def idempotency_cache(self) -> IdempotencyCache:
        return self.app.state.idempotency_cache

    @property
    def tool_registry(self) -> dict[str, Any]:
        return self.app.state.tool_registry

    @property
    def rpc_helper_deps(self) -> Any:
        return self.app.state.rpc_helper_deps

    @property
    def tools_handler_deps(self) -> Any:
        return self.app.state.tools_handler_deps

    # ------------------------------------------------------------------
    # Resilience / rate limit
    # ------------------------------------------------------------------
    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self.app.state.circuit_breaker

    @property
    def auth_rate_limiter(self) -> FixedWindowRateLimiter:
        return self.app.state.auth_rate_limiter

    @property
    def mcp_rate_limiter(self) -> FixedWindowRateLimiter:
        return self.app.state.mcp_rate_limiter

    @property
    def service_rate_limiter(self) -> FixedWindowRateLimiter:
        return self.app.state.service_rate_limiter

    @property
    def oauth_dcr_rate_limiter(self) -> FixedWindowRateLimiter:
        return self.app.state.oauth_dcr_rate_limiter

    @property
    def oauth_cimd_rate_limiter(self) -> FixedWindowRateLimiter:
        return self.app.state.oauth_cimd_rate_limiter

    # ------------------------------------------------------------------
    # OAuth / plugins
    # ------------------------------------------------------------------
    @property
    def oauth(self) -> Any:
        return self.app.state.oauth

    @property
    def plugins(self) -> PluginRegistry:
        return self.app.state.plugins

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------
    @property
    def reaper_task(self) -> asyncio.Task[Any] | None:
        return self.app.state.reaper_task

    @property
    def health_probe_task(self) -> asyncio.Task[Any] | None:
        return self.app.state.health_probe_task


__all__ = ["AppContext"]
