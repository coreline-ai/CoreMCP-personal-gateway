from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from coremcp.auth import (
    CLIENT_TOKEN_PREFIX,
    ClientTokenService,
    OAuthService,
    extract_bearer_token,
    verify_admin_bearer,
)
from coremcp.auth.admin import ADMIN_TOKEN_PREFIX
from coremcp.auth.rate_limit import FixedWindowRateLimiter, build_rate_limiter
from coremcp.api.body_limit import RequestBodyTooLarge, contains_request_body_too_large, install_streaming_body_limit
from coremcp.api import (
    oauth_issuer,
    oauth_resource,
    register_admin_meta_routes,
    register_connections_routes,
    register_mcp_routes,
    register_meta_routes,
    register_oauth_routes,
    register_playground_routes,
    register_services_routes,
    register_simulator_routes,
    register_toolboxes_routes,
)
from coremcp.credentials import build_vault
from coremcp.db import Repository
from coremcp.db.repository_facade import RepositoryFacades
from coremcp.logging import configure_logging
from coremcp.mcp_gateway import (
    IdempotencyCache,
    LIST_CHANGED_CATEGORIES,
    ListChangedCategory,
    ListChangedEventBus,
    SessionStore,
    negotiate_protocol_version,
    reap_stale_inflight,
    protocol_negotiation_warning,
    run_reaper_loop,
)
from coremcp.mcp_gateway.health_probe import (
    detect_service_tool_schema_drift as _detect_service_tool_schema_drift,
    run_service_health_probe_loop as _run_service_health_probe_loop,
    run_service_health_probe_once as _run_service_health_probe_once,
)
from coremcp.mcp_gateway.responses import (
    accepted,
    api_error,
    jsonrpc_error,
    jsonrpc_result,
    not_found,
    tool_error_result,
)
from coremcp.mcp_gateway.stdio_pool import (
    audit_stdio_command_rejected as _audit_stdio_command_rejected,
    close_stdio_client_for_service as _close_stdio_client_for_service,
    persist_stdio_state as _persist_stdio_state,
    stdio_client_for_config as _stdio_client_for_config,
    stdio_command_basename as _stdio_command_basename,
    stdio_default_idle_timeout as _stdio_default_idle_timeout,
    stdio_env as _stdio_env,
    stdio_signature as _stdio_signature,
    validate_stdio_runtime_config as _validate_stdio_runtime_config,
)
from coremcp.mcp_gateway.tool_schema import (
    tool_schema_change_summary as _tool_schema_change_summary,
    tool_schema_diff as _tool_schema_diff,
)
from coremcp.mcp.capabilities import (
    DEFAULT_SERVER_CAPABILITIES,
    server_capabilities_for_default_toolbox as _server_capabilities_for_default_toolbox,
)
from coremcp.mcp.dispatcher import (
    McpDispatchHandlers,
    dispatch_mcp as _dispatch_mcp,
    dispatch_mcp_batch as _dispatch_mcp_batch,
)
from coremcp.mcp.notifications import (
    is_downstream_notification_method,
    list_changed_category_for_method,
    notification_params,
)
from coremcp.mcp.prompts_handlers import (
    PromptsHandlerDeps,
    handle_prompts_get as _mcp_handle_prompts_get,
    handle_prompts_list as _mcp_handle_prompts_list,
)
from coremcp.mcp.resources_handlers import (
    ResourcesHandlerDeps,
    handle_resources_list as _mcp_handle_resources_list,
    handle_resources_read as _mcp_handle_resources_read,
)
from coremcp.mcp.rpc import (
    RpcHelperDeps,
    request_default_downstream_rpc as _mcp_request_default_downstream_rpc,
    request_service_rpc as _mcp_request_service_rpc,
)
from coremcp.mcp.session_proxy import (
    downstream_session_callback as _downstream_session_callback,
    downstream_session_id as _downstream_session_id,
    forget_downstream_session as _forget_downstream_session,
    reap_expired_downstream_sessions as _reap_expired_downstream_sessions,
)
from coremcp.mcp.tools_handlers import (
    ToolsHandlerDeps,
    handle_tools_call as _mcp_handle_tools_call,
    handle_tools_list as _mcp_handle_tools_list,
    refresh_tools as _mcp_refresh_tools,
)
from coremcp.plugins import PluginRegistry
from coremcp.proxy import (
    CircuitBreaker,
    DownstreamMcpClient,
    DownstreamMcpError,
    StdioMcpClient,
    UrlSafetyChecker,
    UrlSafetyError,
)
from coremcp.registry.catalog import normalize_downstream_tools
from coremcp.settings import Settings, get_settings

SERVER_CAPABILITIES = DEFAULT_SERVER_CAPABILITIES
JSONRPC_VERSION = "2.0"
ONE_TIME_TOKEN_PREFIX = "cmcp_otk_"
ONE_TIME_TOKEN_TTL_SECONDS = 600
OAUTH_DCR_RATE_LIMIT = 10
OAUTH_DCR_RATE_LIMIT_WINDOW_SECONDS = 3600
DEFAULT_CLIENT_SCOPES = ["mcp:tools.read", "mcp:tools.call"]
ALLOWED_CLIENT_SCOPES = {"mcp:tools.read", "mcp:tools.call", "mcp:connections.manage"}
TOOL_PERMISSION_LEVELS = {"hidden", "visible_only", "callable"}
TOOL_PRESETS = {"readonly", "full_access", "dangerous_off"}
SERVICE_TRANSPORT_TYPES = {"http", "stdio"}
SESSION_IDLE_REAP_SECONDS = 30 * 60
INFLIGHT_REAP_INTERVAL_SECONDS = 30
JOB_REAP_MAX_AGE_SECONDS = 60 * 60
DANGEROUS_TOOL_KEYWORDS = (
    "delete",
    "remove",
    "drop",
    "destroy",
    "purge",
    "truncate",
    "revoke",
    "disable",
    "shutdown",
)
def correlation_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or request.headers.get("X-Request-ID") or f"req_{secrets.token_hex(16)}")


def unauthorized_response(request: Request | None = None) -> JSONResponse:
    challenge = 'Bearer realm="coremcp", error="invalid_token"'
    if request is not None and request.app.state.settings.auth_mode == "oauth":
        challenge = (
            'Bearer realm="coremcp", '
            f'resource_metadata="{str(request.url_for("protected_resource_metadata"))}", '
            'error="invalid_token"'
        )
    return JSONResponse(
        {"error": "invalid_token"},
        status_code=401,
        headers={"WWW-Authenticate": challenge},
    )


def verify_admin_request(request: Request) -> bool:
    token = extract_bearer_token(request.headers.get("Authorization"))
    return verify_admin_bearer(token, request.app.state.settings)


async def verify_mcp_request(request: Request) -> bool:
    token = extract_bearer_token(request.headers.get("Authorization"))
    token_service = ClientTokenService(request.app.state.repos.credentials)
    client_auth = await token_service.verify(token)
    if client_auth is not None:
        request.state.client_auth = client_auth
        request.state.oauth_claims = None
        request.state.mcp_auth_kind = "client"
        return True
    request.state.client_auth = None
    if request.app.state.settings.auth_mode == "oauth":
        claims = await request.app.state.oauth.verify_access_token(
            token,
            issuer=oauth_issuer(request),
            audience=oauth_resource(request),
        )
        if claims is not None:
            request.state.oauth_claims = claims
            request.state.mcp_auth_kind = "oauth"
            return True
    request.state.oauth_claims = None
    if verify_admin_bearer(token, request.app.state.settings):
        request.state.mcp_auth_kind = "admin"
        return True
    request.state.mcp_auth_kind = None
    return False


def _mcp_has_scope(request: Request, required_scope: str) -> bool:
    auth_kind = getattr(request.state, "mcp_auth_kind", None)
    if auth_kind == "admin":
        return True
    if auth_kind == "client":
        client_auth = getattr(request.state, "client_auth", None)
        return required_scope in set(getattr(client_auth, "scopes", []) or [])
    if auth_kind == "oauth":
        claims = getattr(request.state, "oauth_claims", None) or {}
        scope = claims.get("scope", "") if isinstance(claims, dict) else ""
        return required_scope in set(str(scope).split())
    return False


def request_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _get_request_id(payload: dict[str, Any]) -> Any:
    return payload.get("id")


def _idempotency_cache_key(request: Request, exposed_name: str) -> str | None:
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None
    client_auth = getattr(request.state, "client_auth", None)
    if client_auth is not None:
        bearer_kind = "client"
        identity = getattr(client_auth, "external_connection_id", None) or "unknown"
    elif getattr(request.state, "mcp_auth_kind", None) == "oauth":
        claims = getattr(request.state, "oauth_claims", None) or {}
        client_id = str(claims.get("client_id") or "unknown")
        external_connection_id = str(claims.get("external_connection_id") or "unknown")
        bearer_kind = "oauth"
        identity = f"{client_id}:{external_connection_id}"
    else:
        bearer_kind = "admin"
        identity = "admin"
    return f"{bearer_kind}:{identity}:{exposed_name}:{key}"


async def _scope_denied_response(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
    *,
    required_scope: str,
) -> dict[str, Any]:
    request_id = _get_request_id(payload)
    method = str(payload.get("method") or "")
    raw_params = payload.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    tool_name = params.get("name") if isinstance(params.get("name"), str) else None
    request_log_id = correlation_id(request)
    await app.state.repos.audit.log_audit(
        action="policy.deny",
        resource_type="mcp_scope",
        metadata={
            "method": method,
            "tool": tool_name,
            "required_scope": required_scope,
            "auth_kind": getattr(request.state, "mcp_auth_kind", None),
        },
        request_id=request_log_id,
        ip=request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await app.state.repos.audit.log_invocation(
        session_id=request.headers.get("Mcp-Session-Id"),
        method=method,
        tool_name=tool_name,
        status="policy_denied",
        error_code="insufficient_scope",
        request_id=request_log_id,
        error_message=f"required scope: {required_scope}",
        client_ip=request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return jsonrpc_error(
        request_id,
        -32001,
        f"Required MCP scope is missing: {required_scope}",
        {"required_scope": required_scope},
    )


async def _json_body(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        payload = await request.json()
    except ValueError:
        return api_error("parse_error", "Request body must be valid JSON", status_code=400)
    if not isinstance(payload, dict):
        return api_error("validation_failed", "Request body must be an object", status_code=422)
    return payload


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


def _transport_type(config: dict[str, Any]) -> str:
    transport = str(config.get("transport_type") or "http").lower()
    return transport if transport in SERVICE_TRANSPORT_TYPES else "http"


def _bearer_rate_limit_bucket(request: Request, *, route_kind: str) -> str:
    token = extract_bearer_token(request.headers.get("Authorization"))
    if not token:
        return f"{route_kind}:missing"
    if token.startswith(ADMIN_TOKEN_PREFIX):
        token_kind = "admin"
        token_prefix_label = "cmcp_admin"
    elif token.startswith(CLIENT_TOKEN_PREFIX):
        token_kind = "client"
        token_prefix_label = "cmcp_client"
    elif token.count(".") == 2:
        token_kind = "oauth"
        token_prefix_label = "jwt"
    else:
        token_kind = "bearer"
        token_prefix_label = "unknown"
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return f"{route_kind}:{token_kind}:{token_prefix_label}:{fingerprint}"


def _rate_limit_response(request: Request, *, route_kind: str, retry_after_seconds: int | None) -> JSONResponse:
    retry_after = str(max(1, int(retry_after_seconds or 1)))
    if route_kind == "mcp":
        response = JSONResponse(
            jsonrpc_error(
                None,
                -32029,
                "Rate limit exceeded",
                {"retry_after_seconds": int(retry_after)},
            ),
            status_code=429,
        )
    else:
        response = api_error(
            "rate_limited",
            "Rate limit exceeded",
            status_code=429,
            details={"retry_after_seconds": int(retry_after)},
        )
    response.headers["Retry-After"] = retry_after
    origin = request.headers.get("origin")
    if origin and origin in request.app.state.settings.cors_allowed_origin_list:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    return response


def _rate_limit_tool_error(retry_after_seconds: int | None) -> dict[str, Any]:
    return tool_error_result(
        "rate_limited",
        "Downstream service rate limit exceeded",
        retry_after_seconds=float(retry_after_seconds or 1),
        reason="service_rate_limit_exceeded",
    )


def _check_in_process_rate_limit(request: Request, *, route_kind: str, limit_per_minute: int) -> JSONResponse | None:
    if limit_per_minute <= 0:
        return None
    limiter: FixedWindowRateLimiter = (
        request.app.state.mcp_rate_limiter
        if route_kind == "mcp"
        else request.app.state.auth_rate_limiter
    )
    key = ":".join(
        (
            "rate",
            route_kind,
            request_ip(request) or "unknown",
            _bearer_rate_limit_bucket(request, route_kind=route_kind),
        )
    )
    decision = limiter.check(key, limit=limit_per_minute, window_seconds=60)
    if decision.allowed:
        return None
    return _rate_limit_response(
        request,
        route_kind=route_kind,
        retry_after_seconds=decision.retry_after_seconds,
    )


def _check_service_rate_limit(app: FastAPI, *, service_id: str, method: str, tool_name: str | None = None):
    limit = int(getattr(app.state.settings, "service_rate_limit_per_minute", 0) or 0)
    if limit <= 0 or not service_id:
        return None
    key = ":".join(("service", service_id, method, tool_name or "*"))
    decision = app.state.service_rate_limiter.check(key, limit=limit, window_seconds=60)
    return None if decision.allowed else decision


async def _downstream_headers_for_service(app: FastAPI, service_id: str | None) -> dict[str, str]:
    if not service_id:
        return {}
    credential = await app.state.repos.credentials.get_service_credential(service_id)
    if not credential:
        return {}
    secret = await app.state.vault.get(credential["secret_ref"])
    if not secret:
        return {}
    if credential["credential_type"] == "bearer_token":
        return {"Authorization": f"Bearer {secret}"}
    if credential["credential_type"] == "api_key_header" and credential.get("header_name"):
        return {credential["header_name"]: secret}
    return {}


def _idempotency_downstream_header(request: Request) -> dict[str, str]:
    value = request.headers.get("Idempotency-Key")
    if not isinstance(value, str) or not value.strip():
        return {}
    return {"Idempotency-Key": value.strip()}


def _parse_last_event_id(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value.strip()))
    except ValueError:
        return None


def _invalidate_catalog_caches(app: FastAPI) -> None:
    app.state.tool_registry = {}
    if hasattr(app.state, "idempotency_cache"):
        app.state.idempotency_cache.clear()


def _record_downstream_failure(app: FastAPI, service_id: str) -> None:
    if not service_id:
        return
    snapshot = app.state.circuit_breaker.record_failure(service_id)
    if snapshot.state == "open":
        _forget_downstream_session(app, service_id)


async def _publish_list_changed(
    app: FastAPI,
    *,
    reason: str,
    resource_id: str | None = None,
    categories: tuple[ListChangedCategory, ...] = LIST_CHANGED_CATEGORIES,
) -> None:
    _invalidate_catalog_caches(app)
    # Tool override / preset / catalog 변화는 같은 (token, tool, key) 조합의
    # 캐시된 응답을 stale 로 만든다. permission/visibility 변경 직후 idempotency
    # cache 가 남아 있으면 막힌 도구가 다시 success 응답을 돌려줄 수 있어 명시
    # 무효화한다.
    idempotency_cache = getattr(app.state, "idempotency_cache", None)
    if idempotency_cache is not None:
        idempotency_cache.clear()
    for category in categories:
        await app.state.list_changed_bus.publish_list_changed(
            category=category,
            reason=reason,
            resource_id=resource_id,
        )


async def _publish_downstream_notification(
    app: FastAPI,
    notification: dict[str, Any],
    *,
    service_id: str | None = None,
    source: str = "downstream",
) -> None:
    method = notification.get("method")
    if not is_downstream_notification_method(method):
        return
    safe_params = notification_params(notification)
    metadata: dict[str, Any] = {"source": source}
    if service_id:
        metadata["service_id"] = service_id
    if category := list_changed_category_for_method(method):
        _invalidate_catalog_caches(app)
        metadata["category"] = category
        metadata["reason"] = f"{source}.{category}.list_changed"
        await app.state.list_changed_bus.publish_notification(
            method=method,
            params=safe_params,
            metadata=metadata,
            event_name="listChanged",
        )
        return
    await app.state.list_changed_bus.publish_notification(
        method=method,
        params=safe_params,
        metadata=metadata,
    )


def _downstream_notification_callback(
    app: FastAPI,
    *,
    service_id: str | None = None,
    source: str = "downstream",
):
    async def callback(notification: dict[str, Any]) -> None:
        await _publish_downstream_notification(
            app,
            notification,
            service_id=service_id,
            source=source,
        )

    return callback


def _utc_sql_timestamp(epoch_seconds: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(epoch_seconds))


def _iso_z(sql_timestamp: str) -> str:
    return sql_timestamp.replace(" ", "T") + "Z"


def _generate_one_time_token() -> str:
    return f"{ONE_TIME_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def _prometheus_metrics(snapshot: dict[str, int]) -> str:
    lines = [
        "# HELP coremcp_mcp_services_total Total registered MCP services.",
        "# TYPE coremcp_mcp_services_total gauge",
        f"coremcp_mcp_services_total {snapshot['mcp_services_total']}",
        "# HELP coremcp_mcp_services_active Active MCP services.",
        "# TYPE coremcp_mcp_services_active gauge",
        f"coremcp_mcp_services_active {snapshot['mcp_services_active']}",
        "# HELP coremcp_mcp_services_health_failing Services with one or more consecutive health probe failures.",
        "# TYPE coremcp_mcp_services_health_failing gauge",
        f"coremcp_mcp_services_health_failing {snapshot['mcp_services_health_failing']}",
        "# HELP coremcp_mcp_services_circuit_open Services whose persisted circuit window is currently open.",
        "# TYPE coremcp_mcp_services_circuit_open gauge",
        f"coremcp_mcp_services_circuit_open {snapshot['mcp_services_circuit_open']}",
        "# HELP coremcp_external_connections_active Active external client connections.",
        "# TYPE coremcp_external_connections_active gauge",
        f"coremcp_external_connections_active {snapshot['external_connections_active']}",
        "# HELP coremcp_personal_access_tokens_active Active personal access tokens.",
        "# TYPE coremcp_personal_access_tokens_active gauge",
        f"coremcp_personal_access_tokens_active {snapshot['personal_access_tokens_active']}",
        "# HELP coremcp_mcp_requests_total Total MCP request log records.",
        "# TYPE coremcp_mcp_requests_total counter",
        f"coremcp_mcp_requests_total {snapshot['mcp_requests_total']}",
        "# HELP coremcp_tool_calls_total Total MCP tools/call records.",
        "# TYPE coremcp_tool_calls_total counter",
        f"coremcp_tool_calls_total {snapshot['tool_calls_total']}",
        "# HELP coremcp_tool_call_errors_total Total MCP tools/call non-success records.",
        "# TYPE coremcp_tool_call_errors_total counter",
        f"coremcp_tool_call_errors_total {snapshot['tool_call_errors_total']}",
        "# HELP coremcp_auth_failures_total Total authentication failure audit records.",
        "# TYPE coremcp_auth_failures_total counter",
        f"coremcp_auth_failures_total {snapshot['auth_failures_total']}",
        "# HELP coremcp_policy_denials_total Total policy-denied invocation records.",
        "# TYPE coremcp_policy_denials_total counter",
        f"coremcp_policy_denials_total {snapshot['policy_denials_total']}",
        "# HELP coremcp_active_mcp_sessions Active in-memory MCP sessions.",
        "# TYPE coremcp_active_mcp_sessions gauge",
        f"coremcp_active_mcp_sessions {snapshot['active_mcp_sessions']}",
        "# HELP coremcp_downstream_timeouts_total Total downstream timeout records.",
        "# TYPE coremcp_downstream_timeouts_total counter",
        f"coremcp_downstream_timeouts_total {snapshot['downstream_timeouts_total']}",
        "# HELP coremcp_tool_invocations_total Total recorded tool invocations.",
        "# TYPE coremcp_tool_invocations_total counter",
        f"coremcp_tool_invocations_total {snapshot['tool_invocations_total']}",
        "# HELP coremcp_audit_logs_total Total recorded audit events.",
        "# TYPE coremcp_audit_logs_total counter",
        f"coremcp_audit_logs_total {snapshot['audit_logs_total']}",
        "",
    ]
    return "\n".join(lines)


def _validated_scopes(raw: Any) -> list[str] | None:
    if raw is None:
        return list(DEFAULT_CLIENT_SCOPES)
    if not isinstance(raw, list) or not raw:
        return None
    scopes: list[str] = []
    for item in raw:
        if not isinstance(item, str) or item not in ALLOWED_CLIENT_SCOPES:
            return None
        if item not in scopes:
            scopes.append(item)
    return scopes


def _connection_token_prompt(token: str, expires_at: str) -> str:
    return (
        "Use this CoreMCP one-time connection token in the external MCP client. "
        f"Token: {token}. Expires at: {expires_at}."
    )


def _tool_annotation_bool(tool: dict[str, Any], key: str) -> bool:
    annotations = tool.get("annotations")
    return bool(isinstance(annotations, dict) and annotations.get(key) is True)


def _is_dangerous_tool(tool: dict[str, Any]) -> bool:
    if _tool_annotation_bool(tool, "destructiveHint"):
        return True
    if str(tool.get("risk_level") or "").lower() in {"high", "critical", "dangerous"}:
        return True
    haystack = " ".join(
        str(tool.get(key) or "")
        for key in ("original_name", "exposed_name", "title", "description")
    ).lower()
    return any(keyword in haystack for keyword in DANGEROUS_TOOL_KEYWORDS)


def _tool_preset_policy(tool: dict[str, Any], preset: str) -> tuple[bool, str]:
    dangerous = _is_dangerous_tool(tool)
    if preset == "full_access":
        return True, "callable"
    if preset == "readonly":
        read_only = _tool_annotation_bool(tool, "readOnlyHint") and not dangerous
        return True, "callable" if read_only else "hidden"
    if preset == "dangerous_off":
        return True, "hidden" if dangerous else "callable"
    raise ValueError(f"unsupported tool preset: {preset}")


def _tool_override_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"callable": 0, "visible_only": 0, "hidden": 0, "disabled": 0}
    for item in items:
        if not item.get("enabled", True):
            counts["disabled"] += 1
            continue
        permission = str(item.get("permission_level") or "callable")
        if permission in {"callable", "visible_only", "hidden"}:
            counts[permission] += 1
    return counts


async def _forward_downstream_cancellation(
    app: FastAPI,
    request: Request,
    *,
    params: dict[str, Any],
    request_id: Any,
) -> None:
    cancelled_request_id = params.get("requestId") or request_id or "cancelled"
    inflight = getattr(app.state, "inflight_downstream_calls", {}).get(str(cancelled_request_id), {})
    target_url = inflight.get("url") if isinstance(inflight, dict) else None
    protocol_version = (
        inflight.get("protocol_version")
        if isinstance(inflight, dict) and isinstance(inflight.get("protocol_version"), str)
        else request.headers.get("MCP-Protocol-Version")
    )
    session_id = (
        inflight.get("session_id")
        if isinstance(inflight, dict) and isinstance(inflight.get("session_id"), str)
        else request.headers.get("Mcp-Session-Id")
    )
    downstream_headers = (
        inflight.get("downstream_headers")
        if isinstance(inflight, dict) and isinstance(inflight.get("downstream_headers"), dict)
        else None
    )

    if isinstance(inflight, dict) and _transport_type(inflight) == "stdio":
        try:
            stdio_client = await _stdio_client_for_config(app, inflight)
            await asyncio.wait_for(
                stdio_client.request(
                    method="notifications/cancelled",
                    params=params,
                    request_id=f"cancel-{cancelled_request_id}",
                    protocol_version=protocol_version,
                    session_id=session_id,
                    expect_response=False,
                    correlation_id=correlation_id(request),
                ),
                timeout=2.0,
            )
            await app.state.repos.audit.log_audit(
                action="downstream.cancel.forward",
                resource_type="mcp_service",
                resource_id=inflight.get("service_id"),
                metadata={"request_id": str(cancelled_request_id), "transport_type": "stdio"},
                request_id=correlation_id(request),
                ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        except (TimeoutError, DownstreamMcpError) as exc:
            await app.state.repos.audit.log_audit(
                action="downstream.cancel.forward_failed",
                resource_type="mcp_service",
                resource_id=inflight.get("service_id"),
                metadata={"request_id": str(cancelled_request_id), "reason": str(exc), "transport_type": "stdio"},
                request_id=correlation_id(request),
                ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        return

    checker: UrlSafetyChecker | None = None
    safety_result = None
    if target_url:
        checker = UrlSafetyChecker(app.state.settings)
        try:
            safety_result = checker.assert_safe(target_url)
        except UrlSafetyError as exc:
            await app.state.repos.audit.log_audit(
                action="downstream.cancel.forward_failed",
                resource_type="mcp_service",
                resource_id=inflight.get("service_id") if isinstance(inflight, dict) else None,
                metadata={"request_id": str(cancelled_request_id), "reason": str(exc), "stage": "url_safety"},
                request_id=correlation_id(request),
                ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            return

    try:
        await asyncio.wait_for(
            app.state.downstream.request(
                method="notifications/cancelled",
                params=params,
                request_id=f"cancel-{cancelled_request_id}",
                protocol_version=protocol_version,
                session_id=session_id,
                url=target_url,
                downstream_headers=downstream_headers,
                url_safety_checker=checker,
                safety_result=safety_result,
                expect_response=False,
                correlation_id=correlation_id(request),
            ),
            timeout=2.0,
        )
        await app.state.repos.audit.log_audit(
            action="downstream.cancel.forward",
            resource_type="mcp_service",
            resource_id=inflight.get("service_id") if isinstance(inflight, dict) else None,
            metadata={"request_id": str(cancelled_request_id), "has_inflight_route": bool(target_url)},
            request_id=correlation_id(request),
            ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except TimeoutError:
        await app.state.repos.audit.log_audit(
            action="downstream.cancel.forward_failed",
            resource_type="mcp_service",
            resource_id=inflight.get("service_id") if isinstance(inflight, dict) else None,
            metadata={"request_id": str(cancelled_request_id), "reason": "timeout"},
            request_id=correlation_id(request),
            ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except DownstreamMcpError as exc:
        await app.state.repos.audit.log_audit(
            action="downstream.cancel.forward_failed",
            resource_type="mcp_service",
            resource_id=inflight.get("service_id") if isinstance(inflight, dict) else None,
            metadata={"request_id": str(cancelled_request_id), "reason": str(exc), "code": exc.code},
            request_id=correlation_id(request),
            ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )


def _rpc_helper_deps(app: FastAPI) -> RpcHelperDeps:
    deps = getattr(app.state, "rpc_helper_deps", None)
    if isinstance(deps, RpcHelperDeps):
        return deps
    deps = RpcHelperDeps(
        settings=app.state.settings,
        downstream=app.state.downstream,
        stdio_client_for_config=lambda config: _stdio_client_for_config(app, config),
        downstream_headers_for_service=lambda service_id: _downstream_headers_for_service(app, service_id),
        downstream_session_id=lambda service_id: _downstream_session_id(app, service_id),
        downstream_session_callback=lambda service_id: _downstream_session_callback(app, service_id),
        downstream_notification_callback=lambda service_id, source: _downstream_notification_callback(
            app,
            service_id=service_id,
            source=source,
        ),
    )
    app.state.rpc_helper_deps = deps
    return deps


async def _request_service_rpc(
    app: FastAPI,
    service: dict[str, Any],
    *,
    method: str,
    params: dict[str, Any] | None,
    request_id: Any,
    protocol_version: str | None,
    session_id: str | None = None,
    correlation_id_value: str | None = None,
    timeout: httpx.Timeout | float | None = None,
    send_downstream_session: bool = True,
) -> dict[str, Any]:
    return await _mcp_request_service_rpc(
        _rpc_helper_deps(app),
        service,
        method=method,
        params=params,
        request_id=request_id,
        protocol_version=protocol_version,
        session_id=session_id,
        correlation_id_value=correlation_id_value,
        timeout=timeout,
        send_downstream_session=send_downstream_session,
    )


async def _request_default_downstream_rpc(
    app: FastAPI,
    *,
    method: str,
    params: dict[str, Any] | None,
    request_id: Any,
    protocol_version: str | None,
    session_id: str | None,
    correlation_id_value: str | None,
    timeout: httpx.Timeout | float | None = None,
    send_downstream_session: bool = True,
) -> dict[str, Any]:
    return await _mcp_request_default_downstream_rpc(
        _rpc_helper_deps(app),
        method=method,
        params=params,
        request_id=request_id,
        protocol_version=protocol_version,
        session_id=session_id,
        correlation_id_value=correlation_id_value,
        timeout=timeout,
        send_downstream_session=send_downstream_session,
    )


def _service_config_from_catalog_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("service_id"),
        "service_id": row.get("service_id"),
        "slug": row.get("service_slug"),
        "endpoint_url": row.get("endpoint_url"),
        "transport_type": row.get("transport_type") or "http",
        "stdio_command": row.get("stdio_command"),
        "stdio_args": row.get("stdio_args") or [],
        "stdio_env": row.get("stdio_env") or {},
        "stdio_cwd": row.get("stdio_cwd"),
        "stdio_idle_timeout_seconds": row.get("stdio_idle_timeout_seconds"),
    }


async def _run_ops_reapers_once(app: FastAPI) -> None:
    async def session_reap() -> int:
        return app.state.sessions.reap_idle(SESSION_IDLE_REAP_SECONDS)

    def inflight_reap():
        return reap_stale_inflight(
            app.state.inflight_downstream_calls,
            timeout_multiplier=2.0,
            remove_malformed=False,
        )

    async def stuck_job_cleanup() -> int:
        marked = await app.state.repos.jobs.mark_stuck_jobs_failed(max_age_seconds=JOB_REAP_MAX_AGE_SECONDS)
        return marked + _reap_expired_downstream_sessions(app)

    await run_reaper_loop(
        interval_seconds=INFLIGHT_REAP_INTERVAL_SECONDS,
        session_reap=session_reap,
        inflight_reap=inflight_reap,
        stuck_job_cleanup=stuck_job_cleanup,
        run_immediately=False,
        on_error=lambda exc: None,
    )


async def _handle_initialize(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
) -> tuple[dict[str, Any], str]:
    raw_params = payload.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    requested_raw = params.get("protocolVersion") or request.headers.get("MCP-Protocol-Version")
    requested = str(requested_raw) if requested_raw is not None else None
    protocol_version = negotiate_protocol_version(requested)
    session = app.state.sessions.create(protocol_version)

    downstream_params: dict[str, Any] = dict(params)
    downstream_params["protocolVersion"] = protocol_version
    init_timeout_seconds = max(0.1, float(app.state.settings.initialize_downstream_timeout_seconds))
    init_timeout = httpx.Timeout(
        init_timeout_seconds,
        connect=min(float(app.state.settings.downstream_connect_timeout_seconds), init_timeout_seconds),
        read=init_timeout_seconds,
        write=init_timeout_seconds,
        pool=init_timeout_seconds,
    )
    try:
        await app.state.downstream.request(
            method="initialize",
            params=downstream_params,
            request_id=_get_request_id(payload),
            protocol_version=protocol_version,
            session_id=_downstream_session_id(app, None),
            correlation_id=correlation_id(request),
            timeout=init_timeout,
            session_id_callback=_downstream_session_callback(app, None),
            notification_callback=_downstream_notification_callback(app, source="http"),
        )
    except DownstreamMcpError:
        # Best-effort compatibility probe only: registered services validate
        # independently, so initialize must not block clients on a slow default
        # fake/downstream endpoint.
        pass

    result = {
        "protocolVersion": protocol_version,
        "capabilities": await _server_capabilities_for_default_toolbox(app),
        "serverInfo": {"name": "CoreMCP", "version": app.state.settings.app_version},
    }
    if warning := protocol_negotiation_warning(requested, protocol_version):
        result["_coremcp"] = warning
    return jsonrpc_result(_get_request_id(payload), result), session.id


async def _refresh_resource_prompt_catalog(
    app: FastAPI,
    service: dict[str, Any],
    *,
    protocol_version: str | None,
    correlation_id_value: str | None,
) -> dict[str, Any]:
    summary = {
        "resources_found": 0,
        "resource_templates_found": 0,
        "prompts_found": 0,
        "resources_supported": False,
        "resource_templates_supported": False,
        "prompts_supported": False,
    }

    async def request_list(method: str, result_key: str) -> list[dict[str, Any]]:
        try:
            response = await _request_service_rpc(
                app,
                service,
                method=method,
                params={},
                request_id=f"validate-{service['id']}-{method}",
                protocol_version=protocol_version,
                correlation_id_value=correlation_id_value,
            )
        except DownstreamMcpError as exc:
            if exc.code == -32601:
                return []
            return []
        result = response.get("result")
        items = result.get(result_key) if isinstance(result, dict) else None
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    resources = await request_list("resources/list", "resources")
    if resources:
        summary["resources_supported"] = True
    saved_resources = await app.state.repos.catalog.replace_service_resources(str(service["id"]), resources)
    summary["resources_found"] = len(saved_resources)

    templates = await request_list("resources/templates/list", "resourceTemplates")
    if templates:
        summary["resource_templates_supported"] = True
    saved_templates = await app.state.repos.catalog.replace_service_resource_templates(str(service["id"]), templates)
    summary["resource_templates_found"] = len(saved_templates)

    prompts = await request_list("prompts/list", "prompts")
    if prompts:
        summary["prompts_supported"] = True
    saved_prompts = await app.state.repos.catalog.replace_service_prompts(str(service["id"]), prompts)
    summary["prompts_found"] = len(saved_prompts)

    return summary


async def _handle_tools_call(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    return await _mcp_handle_tools_call(
        app,
        payload,
        request,
        deps=app.state.tools_handler_deps,
    )


async def validate_service(
    app: FastAPI,
    service_id: str,
    *,
    job_id: str | None = None,
    correlation_id_value: str | None = None,
) -> dict[str, Any]:
    repos = app.state.repos
    service = await repos.services.get_mcp_service(service_id)
    if service is None:
        raise ValueError("service not found")

    stages: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    protocol_version = "2025-11-25"
    stdio_client: StdioMcpClient | None = None
    try:
        await repos.services.update_mcp_service(service_id, {"status": "validating"})
        if job_id:
            await repos.jobs.update_job(job_id, status="running", progress=0.2)

        transport_type = _transport_type(service)
        if transport_type == "stdio":
            _stdio_signature(service, app.state.settings)
            stdio_client = await _stdio_client_for_config(app, service)
            stages.append({"name": "stdio_config_check", "status": "success"})
        else:
            checker = UrlSafetyChecker(app.state.settings)
            checker.assert_safe(service["endpoint_url"])
            stages.append({"name": "url_safety_check", "status": "success"})

        init_response = await _request_service_rpc(
            app,
            service,
            method="initialize",
            params={"protocolVersion": protocol_version, "capabilities": {}, "clientInfo": {"name": "coremcp-validator", "version": app.state.settings.app_version}},
            request_id=f"validate-{service_id}-init",
            protocol_version=protocol_version,
            correlation_id_value=correlation_id_value,
            send_downstream_session=False,
        )
        init_result = init_response.get("result") if isinstance(init_response, dict) else {}
        downstream_capabilities = (
            init_result.get("capabilities")
            if isinstance(init_result, dict) and isinstance(init_result.get("capabilities"), dict)
            else {}
        )
        if isinstance(init_result, dict) and init_result.get("protocolVersion"):
            protocol_version = str(init_result["protocolVersion"])
        stages.append({"name": "mcp_initialize", "status": "success"})
        if job_id:
            await repos.jobs.update_job(job_id, status="running", progress=0.5)

        tools_response = await _request_service_rpc(
            app,
            service,
            method="tools/list",
            params={},
            request_id=f"validate-{service_id}-tools",
            protocol_version=protocol_version,
            correlation_id_value=correlation_id_value,
        )
        result = tools_response.get("result")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise DownstreamMcpError("downstream tools/list returned invalid tools")
        stages.append({"name": "tools_list", "status": "success", "tools_found": len(tools)})

        existing_tools = await repos.catalog.list_service_tools(service_id)
        normalized, metadata_warnings = normalize_downstream_tools(tools, service_slug=service["slug"], settings=app.state.settings)
        warnings.extend(metadata_warnings)
        if tools and not normalized:
            raise DownstreamMcpError("downstream tools/list returned no valid tools", code=-32602)
        schema_diff = _tool_schema_diff(existing_tools, normalized)
        change_summary = schema_diff["summary"]
        saved = await repos.catalog.replace_service_tools(service_id, normalized)
        catalog_summary = await _refresh_resource_prompt_catalog(
            app,
            service,
            protocol_version=protocol_version,
            correlation_id_value=correlation_id_value,
        )
        stages.append({"name": "metadata_scan", "status": "success", "warnings": warnings, **change_summary})
        stages.append({"name": "resource_prompt_catalog", "status": "success", **catalog_summary})

        summary = {
            "stages": stages,
            "tools_found": len(saved),
            "warnings": warnings,
            "schema_drift": change_summary,
            "schema_diff": schema_diff["details"],
            "resource_prompt_catalog": catalog_summary,
            "downstream_capabilities": downstream_capabilities,
        }
        await repos.services.mark_service_validated(
            service_id=service_id,
            status="active",
            protocol_version=protocol_version,
            summary=summary,
            capabilities=downstream_capabilities,
        )
        await repos.catalog.apply_resource_shadow_policy(service_id)
        await repos.audit.log_audit(
            action="service.validate.success",
            resource_type="mcp_service",
            resource_id=service_id,
            metadata=summary,
            request_id=correlation_id_value,
        )
        if job_id:
            await repos.jobs.update_job(job_id, status="success", progress=1.0, result=summary)
        await _publish_list_changed(app, reason="service.validate.success", resource_id=service_id)
        return {"service_id": service_id, "status": "success", **summary}
    except UrlSafetyError as exc:
        summary = {"stages": stages + [{"name": "url_safety_check", "status": "failed"}], "tools_found": 0, "warnings": warnings, "error": str(exc)}
        await repos.services.mark_service_validated(service_id=service_id, status="error", protocol_version=None, summary=summary)
        await repos.audit.log_audit(
            action="ssrf.block",
            resource_type="mcp_service",
            resource_id=service_id,
            metadata={"url": service["endpoint_url"], "reason": str(exc)},
            request_id=correlation_id_value,
        )
        if job_id:
            await repos.jobs.update_job(job_id, status="failed", progress=1.0, error=summary)
        await _publish_list_changed(app, reason="service.validate.failed", resource_id=service_id)
        raise
    except DownstreamMcpError as exc:
        summary = {"stages": stages, "tools_found": 0, "warnings": warnings, "error": str(exc)}
        existing_tools = await repos.catalog.list_service_tools(service_id)
        preserve_active_catalog = bool(existing_tools and service["status"] == "active")
        await repos.services.mark_service_validated(
            service_id=service_id,
            status="active" if preserve_active_catalog else "error",
            protocol_version=protocol_version,
            summary={**summary, "preserved_active_catalog": preserve_active_catalog},
        )
        await repos.audit.log_audit(
            action="ssrf.block" if exc.code == -32003 else "service.validate.failed",
            resource_type="mcp_service",
            resource_id=service_id,
            metadata={**summary, "preserved_active_catalog": preserve_active_catalog},
            request_id=correlation_id_value,
        )
        if job_id:
            await repos.jobs.update_job(job_id, status="failed", progress=1.0, error={**summary, "preserved_active_catalog": preserve_active_catalog})
        await _publish_list_changed(app, reason="service.validate.failed", resource_id=service_id)
        raise
    finally:
        if stdio_client is not None:
            await _persist_stdio_state(app, service_id, stdio_client)


def create_app(settings: Settings | None = None, http_client: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    owns_http_client = http_client is None
    timeout = httpx.Timeout(
        timeout=settings.downstream_timeout_seconds,
        connect=settings.downstream_connect_timeout_seconds,
        read=settings.downstream_read_timeout_seconds,
    )
    http_client = http_client or httpx.AsyncClient(timeout=timeout)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        app.state.settings = settings
        app.state.sessions = SessionStore()
        app.state.repository = Repository(settings.resolved_database_path)
        app.state.repos = RepositoryFacades(app.state.repository)
        app.state.http_client = http_client
        app.state.downstream = DownstreamMcpClient(
            settings.fake_mcp_url,
            http_client,
            max_response_bytes=settings.downstream_max_response_bytes,
        )
        app.state.tool_registry = {}
        app.state.list_changed_bus = ListChangedEventBus()
        app.state.idempotency_cache = IdempotencyCache()
        app.state.inflight_downstream_calls = {}
        app.state.downstream_sessions = {}
        app.state.stdio_clients = {}
        app.state.stdio_clients_lock = asyncio.Lock()
        app.state.circuit_breaker = CircuitBreaker()
        rate_limit_factory = lambda: build_rate_limiter(  # noqa: E731 - inline closure preserves call shape
            settings.rate_limit_backend,
            redis_url=settings.rate_limit_redis_url,
        )
        app.state.auth_rate_limiter = rate_limit_factory()
        app.state.mcp_rate_limiter = rate_limit_factory()
        app.state.service_rate_limiter = rate_limit_factory()
        app.state.oauth_dcr_rate_limiter = rate_limit_factory()
        app.state.oauth_cimd_rate_limiter = rate_limit_factory()
        app.state.plugins = PluginRegistry()
        app.state.reaper_task = None
        app.state.health_probe_task = None
        app.state.vault = build_vault(settings)
        await app.state.repository.connect()
        await app.state.vault.is_ready()
        app.state.oauth = OAuthService(
            settings,
            app.state.repository,
            http_client,
            cimd_rate_limiter=app.state.oauth_cimd_rate_limiter,
            vault=app.state.vault,
        )
        if settings.auth_mode == "oauth":
            await app.state.oauth.startup()
        app.state.reaper_task = asyncio.create_task(_run_ops_reapers_once(app))
        if settings.service_health_probe_enabled:
            app.state.health_probe_task = asyncio.create_task(_run_service_health_probe_loop(app))
        try:
            yield
        finally:
            background_tasks = [
                task
                for task in (
                    getattr(app.state, "reaper_task", None),
                    getattr(app.state, "health_probe_task", None),
                )
                if task is not None
            ]
            for task in background_tasks:
                task.cancel()
            if background_tasks:
                await asyncio.gather(*background_tasks, return_exceptions=True)
            for _, client in list(app.state.stdio_clients.values()):
                await client.aclose()
            app.state.stdio_clients.clear()
            oauth_service = getattr(app.state, "oauth", None)
            if oauth_service is not None:
                await oauth_service.shutdown()
            await app.state.repository.close()
            if owns_http_client:
                await http_client.aclose()

    app = FastAPI(title="CoreMCP API", version=settings.app_version, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origin_list,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Mcp-Session-Id",
            "MCP-Protocol-Version",
            "Idempotency-Key",
            "X-Request-ID",
        ],
        expose_headers=["Mcp-Session-Id", "X-Request-ID"],
        max_age=600,
    )
    # OAuth metadata derives issuer/resource from request host; reject untrusted hosts early.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID")
        request_id = incoming.strip() if incoming and incoming.strip() else f"req_{secrets.token_hex(16)}"
        request.state.request_id = request_id
        def request_too_large_response() -> JSONResponse:
            return api_error(
                "request_too_large",
                f"request body exceeds {settings.max_request_body_bytes} bytes",
                status_code=413,
            )

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                response = api_error("invalid_content_length", "Content-Length must be an integer", status_code=400)
                response.headers["X-Request-ID"] = request_id
                return response
            if length > settings.max_request_body_bytes:
                response = request_too_large_response()
                response.headers["X-Request-ID"] = request_id
                return response
        install_streaming_body_limit(request, max_bytes=settings.max_request_body_bytes)
        if request.method != "OPTIONS":
            rate_limited: JSONResponse | None = None
            if request.url.path == "/mcp":
                rate_limited = _check_in_process_rate_limit(
                    request,
                    route_kind="mcp",
                    limit_per_minute=settings.mcp_rate_limit_per_minute,
                )
            elif request.url.path.startswith("/v1/"):
                rate_limited = _check_in_process_rate_limit(
                    request,
                    route_kind="admin",
                    limit_per_minute=settings.auth_rate_limit_per_minute,
                )
            if rate_limited is not None:
                rate_limited.headers["X-Request-ID"] = request_id
                return rate_limited
        try:
            response = await call_next(request)
        except RequestBodyTooLarge:
            response = request_too_large_response()
            response.headers["X-Request-ID"] = request_id
            return response
        except BaseExceptionGroup as exc:
            if not contains_request_body_too_large(exc):
                raise
            response = request_too_large_response()
            response.headers["X-Request-ID"] = request_id
            return response
        response.headers["X-Request-ID"] = request_id
        return response

    register_meta_routes(app, prometheus_metrics=_prometheus_metrics)

    register_oauth_routes(
        app,
        request_ip=request_ip,
        dcr_rate_limit=OAUTH_DCR_RATE_LIMIT,
        dcr_rate_limit_window_seconds=OAUTH_DCR_RATE_LIMIT_WINDOW_SECONDS,
    )

    tools_handler_deps = ToolsHandlerDeps(
        get_request_id=_get_request_id,
        jsonrpc_result=jsonrpc_result,
        jsonrpc_error=jsonrpc_error,
        request_ip=request_ip,
        correlation_id=correlation_id,
        downstream_session_id=_downstream_session_id,
        downstream_session_callback=_downstream_session_callback,
        downstream_notification_callback=_downstream_notification_callback,
        tool_error_result=tool_error_result,
        idempotency_cache_key=_idempotency_cache_key,
        check_service_rate_limit=_check_service_rate_limit,
        rate_limit_tool_error=_rate_limit_tool_error,
        transport_type=_transport_type,
        stdio_client_for_config=_stdio_client_for_config,
        downstream_headers_for_service=_downstream_headers_for_service,
        idempotency_downstream_header=_idempotency_downstream_header,
        record_downstream_failure=_record_downstream_failure,
        persist_stdio_state=_persist_stdio_state,
    )
    app.state.tools_handler_deps = tools_handler_deps
    resources_handler_deps = ResourcesHandlerDeps(
        get_request_id=_get_request_id,
        jsonrpc_result=jsonrpc_result,
        jsonrpc_error=jsonrpc_error,
        request_service_rpc=_request_service_rpc,
        request_default_downstream_rpc=_request_default_downstream_rpc,
        service_config_from_catalog_row=_service_config_from_catalog_row,
        correlation_id=correlation_id,
    )
    prompts_handler_deps = PromptsHandlerDeps(
        get_request_id=_get_request_id,
        jsonrpc_result=jsonrpc_result,
        jsonrpc_error=jsonrpc_error,
        request_service_rpc=_request_service_rpc,
        request_default_downstream_rpc=_request_default_downstream_rpc,
        service_config_from_catalog_row=_service_config_from_catalog_row,
        correlation_id=correlation_id,
    )

    async def handle_resources_list_route(
        app_: FastAPI,
        payload: dict[str, Any],
        request_: Request,
        *,
        method: str = "resources/list",
        result_key: str = "resources",
    ):
        return await _mcp_handle_resources_list(
            app_,
            payload,
            request_,
            deps=resources_handler_deps,
            method=method,
            result_key=result_key,
        )

    async def handle_resources_read_route(app_: FastAPI, payload: dict[str, Any], request_: Request):
        return await _mcp_handle_resources_read(app_, payload, request_, deps=resources_handler_deps)

    async def handle_prompts_list_route(app_: FastAPI, payload: dict[str, Any], request_: Request):
        return await _mcp_handle_prompts_list(app_, payload, request_, deps=prompts_handler_deps)

    async def handle_prompts_get_route(app_: FastAPI, payload: dict[str, Any], request_: Request):
        return await _mcp_handle_prompts_get(app_, payload, request_, deps=prompts_handler_deps)

    async def handle_tools_list_route(app_: FastAPI, payload: dict[str, Any], request_: Request):
        return await _mcp_handle_tools_list(app_, payload, request_, deps=tools_handler_deps)

    async def refresh_tools_route(
        app_: FastAPI,
        *,
        request_id: Any,
        protocol_version: str | None,
        session_id: str | None,
        params: dict[str, Any] | None = None,
        correlation_id_value: str | None = None,
    ):
        return await _mcp_refresh_tools(
            app_,
            deps=tools_handler_deps,
            request_id=request_id,
            protocol_version=protocol_version,
            session_id=session_id,
            params=params,
            correlation_id_value=correlation_id_value,
        )

    mcp_dispatch_handlers = McpDispatchHandlers(
        jsonrpc_version=JSONRPC_VERSION,
        get_request_id=_get_request_id,
        jsonrpc_error=jsonrpc_error,
        jsonrpc_result=jsonrpc_result,
        has_scope=_mcp_has_scope,
        scope_denied_response=_scope_denied_response,
        request_ip=request_ip,
        handle_initialize=_handle_initialize,
        forward_downstream_cancellation=_forward_downstream_cancellation,
        handle_tools_list=handle_tools_list_route,
        handle_tools_call=_handle_tools_call,
        handle_resources_list=handle_resources_list_route,
        handle_resources_read=handle_resources_read_route,
        handle_prompts_list=handle_prompts_list_route,
        handle_prompts_get=handle_prompts_get_route,
    )

    async def dispatch_mcp_route(app_: FastAPI, payload: dict[str, Any], request_: Request):
        return await _dispatch_mcp(app_, payload, request_, handlers=mcp_dispatch_handlers)

    async def dispatch_mcp_batch_route(app_: FastAPI, payloads: list[Any], request_: Request):
        return await _dispatch_mcp_batch(app_, payloads, request_, handlers=mcp_dispatch_handlers)

    register_mcp_routes(
        app,
        verify_mcp_request=verify_mcp_request,
        unauthorized_response=unauthorized_response,
        jsonrpc_error=jsonrpc_error,
        dispatch_mcp=dispatch_mcp_route,
        dispatch_mcp_batch=dispatch_mcp_batch_route,
        parse_last_event_id=_parse_last_event_id,
        correlation_id=correlation_id,
        request_ip=request_ip,
        session_idle_reap_seconds=SESSION_IDLE_REAP_SECONDS,
    )

    register_services_routes(
        app,
        verify_admin_request=verify_admin_request,
        unauthorized_response=unauthorized_response,
        json_body=_json_body,
        api_error=api_error,
        not_found=not_found,
        accepted=accepted,
        request_ip=request_ip,
        correlation_id=correlation_id,
        validate_service=validate_service,
        validate_stdio_runtime_config=_validate_stdio_runtime_config,
        audit_stdio_command_rejected=_audit_stdio_command_rejected,
        close_stdio_client_for_service=_close_stdio_client_for_service,
        forget_downstream_session=_forget_downstream_session,
        publish_list_changed=_publish_list_changed,
        tool_preset_policy=_tool_preset_policy,
        tool_override_counts=_tool_override_counts,
        string_list=_string_list,
        stdio_env=_stdio_env,
        positive_int=_positive_int,
        stdio_default_idle_timeout=_stdio_default_idle_timeout,
        service_transport_types=SERVICE_TRANSPORT_TYPES,
        tool_permission_levels=TOOL_PERMISSION_LEVELS,
        tool_presets=TOOL_PRESETS,
    )

    register_connections_routes(
        app,
        verify_admin_request=verify_admin_request,
        unauthorized_response=unauthorized_response,
        json_body=_json_body,
        api_error=api_error,
        accepted=accepted,
        request_ip=request_ip,
        validated_scopes=_validated_scopes,
        generate_one_time_token=_generate_one_time_token,
        utc_sql_timestamp=_utc_sql_timestamp,
        iso_z=_iso_z,
        connection_token_prompt=_connection_token_prompt,
        one_time_token_prefix=ONE_TIME_TOKEN_PREFIX,
        one_time_token_ttl_seconds=ONE_TIME_TOKEN_TTL_SECONDS,
    )

    register_toolboxes_routes(
        app,
        verify_admin_request=verify_admin_request,
        unauthorized_response=unauthorized_response,
        json_body=_json_body,
        api_error=api_error,
        not_found=not_found,
        accepted=accepted,
        publish_list_changed=_publish_list_changed,
    )

    register_playground_routes(
        app,
        verify_admin_request=verify_admin_request,
        unauthorized_response=unauthorized_response,
        json_body=_json_body,
        api_error=api_error,
        refresh_tools=refresh_tools_route,
        handle_tools_call=_handle_tools_call,
        correlation_id=correlation_id,
    )

    register_simulator_routes(
        app,
        verify_admin_request=verify_admin_request,
        unauthorized_response=unauthorized_response,
        json_body=_json_body,
        api_error=api_error,
        request_ip=request_ip,
        correlation_id=correlation_id,
    )

    register_admin_meta_routes(
        app,
        verify_admin_request=verify_admin_request,
        unauthorized_response=unauthorized_response,
        api_error=api_error,
        not_found=not_found,
        request_ip=request_ip,
        correlation_id=correlation_id,
    )

    return app


app = create_app()
