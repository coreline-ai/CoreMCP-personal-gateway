from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
from jsonschema import SchemaError, ValidationError
from jsonschema.validators import validator_for
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from coremcp.auth import (
    CLIENT_TOKEN_PREFIX,
    ClientTokenService,
    OAuthError,
    OAuthService,
    extract_bearer_token,
    hash_token,
    verify_admin_bearer,
)
from coremcp.auth.admin import (
    ADMIN_TOKEN_PREFIX,
    AdminTokenFileError,
    generate_admin_token,
    write_admin_token_atomic,
)
from coremcp.auth.rate_limit import FixedWindowRateLimiter
from coremcp.credentials import build_vault, mask_secret
from coremcp.db import DEFAULT_TOOLBOX_ID, Repository
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
from coremcp.proxy import (
    CircuitBreaker,
    CircuitOpenError,
    DownstreamMcpClient,
    DownstreamMcpError,
    DownstreamTimeoutError,
    DownstreamToolError,
    StdioMcpClient,
    UrlSafetyChecker,
    UrlSafetyError,
)
from coremcp.registry.catalog import catalog_row_to_mcp_tool, normalize_downstream_tools, slugify_tool_name
from coremcp.settings import Settings, get_settings

SERVER_CAPABILITIES = {
    "tools": {"listChanged": True},
    "resources": {"listChanged": True, "subscribe": False},
    "prompts": {"listChanged": True},
}
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
SERVICE_HEALTH_FAILURE_THRESHOLD = 3
SERVICE_HEALTH_CIRCUIT_OPEN_SECONDS = 30
RESOURCE_READ_MAX_TEXT_CHARS = 20_000
RESOURCE_READ_MAX_BLOB_CHARS = 1_000_000
DEFAULT_DOWNSTREAM_SESSION_KEY = "__default__"
LIST_CHANGED_METHOD_CATEGORIES: dict[str, ListChangedCategory] = {
    "notifications/tools/list_changed": "tools",
    "notifications/resources/list_changed": "resources",
    "notifications/prompts/list_changed": "prompts",
}
DOWNSTREAM_NOTIFICATION_METHODS = {
    "notifications/progress",
    "notifications/resources/updated",
    *LIST_CHANGED_METHOD_CATEGORIES.keys(),
}
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
BLOCKED_STDIO_ENV_KEYS = {
    "authorization",
    "coremcp_admin_token",
    "coremcp_client_token",
    "coremcp_admin_token_value",
}


def jsonrpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def jsonrpc_error(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def api_error(code: str, message: str, *, status_code: int = 400, details: Any | None = None) -> JSONResponse:
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return JSONResponse(payload, status_code=status_code)


def accepted(payload: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(payload or {"status": "accepted"}, status_code=202)


def not_found(resource: str = "resource") -> JSONResponse:
    return api_error("not_found", f"{resource} not found", status_code=404)


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
    token_service = ClientTokenService(request.app.state.repository)
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


def oauth_issuer(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def oauth_resource(request: Request) -> str:
    return str(request.url_for("mcp"))


def tool_error_result(
    error_code: str,
    message: str,
    *,
    downstream_code: int | None = None,
    reason: str | None = None,
    retry_after_seconds: float | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"error_code": error_code}
    if downstream_code is not None:
        meta["downstream_code"] = downstream_code
    if reason is not None:
        meta["reason"] = reason
    if retry_after_seconds is not None:
        meta["retry_after_seconds"] = retry_after_seconds
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
        "_meta": {"coremcp": meta},
    }


def _normalize_downstream_tool(tool: dict[str, Any]) -> tuple[dict[str, Any], str]:
    original_name = str(tool.get("name", "")).strip()
    exposed_name = f"fake.{slugify_tool_name(original_name)}"
    normalized = dict(tool)
    normalized["name"] = exposed_name
    return normalized, original_name


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
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    tool_name = params.get("name") if isinstance(params.get("name"), str) else None
    request_log_id = correlation_id(request)
    await app.state.repository.log_audit(
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
    await app.state.repository.log_invocation(
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


async def _form_or_json_body(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise OAuthError("invalid_request", "request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise OAuthError("invalid_request", "request body must be an object")
        return payload
    raw = (await request.body()).decode("utf-8")
    parsed = parse_qs(raw, keep_blank_values=True)
    for key, values in parsed.items():
        if len(values) > 1:
            raise OAuthError("invalid_request", f"duplicate form field: {key}")
    return {key: values[0] if values else "" for key, values in parsed.items()}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _stdio_env(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    env: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            continue
        normalized = key.lower().replace("-", "_")
        if (
            normalized in BLOCKED_STDIO_ENV_KEYS
            or normalized.startswith("coremcp_admin_token")
            or normalized.startswith("coremcp_client_token")
            or "authorization" in normalized
        ):
            continue
        env[key] = item
    return env


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


def _transport_type(config: dict[str, Any]) -> str:
    transport = str(config.get("transport_type") or "http").lower()
    return transport if transport in SERVICE_TRANSPORT_TYPES else "http"


def _validate_stdio_runtime_config(command: str | None, cwd: str | None = None) -> str | None:
    if not command or not command.strip():
        return "stdio_command is required for stdio transport"
    command_path = Path(command.strip()).expanduser()
    if not command_path.is_absolute():
        return "stdio_command must be an absolute path"
    if cwd and cwd.strip():
        cwd_path = Path(cwd.strip()).expanduser()
        if not cwd_path.is_absolute():
            return "stdio_cwd must be an absolute path"
        if not cwd_path.exists() or not cwd_path.is_dir():
            return "stdio_cwd must be an existing directory"
    return None


def _stdio_default_idle_timeout(settings: Settings) -> int:
    return max(1, int(settings.stdio_default_idle_timeout_seconds))


def _stdio_signature(config: dict[str, Any], settings: Settings | None = None) -> tuple[Any, ...]:
    command = str(config.get("stdio_command") or "").strip()
    cwd = str(config.get("stdio_cwd") or "").strip() or None
    validation_error = _validate_stdio_runtime_config(command, cwd)
    if validation_error:
        raise DownstreamMcpError(validation_error, code=-32602)
    args = tuple(_string_list(config.get("stdio_args")))
    env = _stdio_env(config.get("stdio_env"))
    settings = settings or get_settings()
    idle_timeout = _positive_int(
        config.get("stdio_idle_timeout_seconds"),
        _stdio_default_idle_timeout(settings),
    )
    return (command, args, tuple(sorted(env.items())), cwd, idle_timeout)


def _stdio_snapshot_sort_key(snapshot: dict[str, Any]) -> float:
    value = snapshot.get("last_used_at") or snapshot.get("started_at") or 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def _ensure_stdio_client_capacity_locked(
    app: FastAPI,
    *,
    service_key: str,
    clients: dict[str, tuple[tuple[Any, ...], StdioMcpClient]],
) -> None:
    max_processes = int(app.state.settings.stdio_max_concurrent_processes)
    if max_processes < 1:
        raise DownstreamMcpError(
            "CoreMCP stdio process capacity exceeded: maximum concurrent processes is 0",
            code=-32010,
        )

    while len(clients) >= max_processes:
        candidates: list[tuple[float, str, StdioMcpClient]] = []
        for key, (_, candidate) in clients.items():
            if key == service_key:
                continue
            snapshot = candidate.snapshot()
            if int(snapshot.get("pending_requests") or 0) > 0:
                continue
            candidates.append((_stdio_snapshot_sort_key(snapshot), key, candidate))

        if not candidates:
            raise DownstreamMcpError(
                "CoreMCP stdio process capacity exceeded and no idle stdio client can be evicted",
                code=-32010,
            )

        _, evicted_key, evicted_client = min(candidates, key=lambda item: item[0])
        clients.pop(evicted_key, None)
        await evicted_client.aclose()


async def _close_stdio_client_for_service(app: FastAPI, service_id: str | None) -> None:
    if not service_id:
        return
    clients: dict[str, tuple[tuple[Any, ...], StdioMcpClient]] = app.state.stdio_clients
    lock: asyncio.Lock | None = getattr(app.state, "stdio_clients_lock", None)
    if lock is None:
        entry = clients.pop(str(service_id), None)
        if entry is not None:
            await entry[1].aclose()
        return
    async with lock:
        entry = clients.pop(str(service_id), None)
    if entry is not None:
        await entry[1].aclose()


async def _stdio_client_for_config(app: FastAPI, config: dict[str, Any]) -> StdioMcpClient:
    signature = _stdio_signature(config, app.state.settings)
    service_key = str(config.get("service_id") or config.get("id") or config.get("endpoint_url") or signature[0])
    clients: dict[str, tuple[tuple[Any, ...], StdioMcpClient]] = app.state.stdio_clients
    lock: asyncio.Lock | None = getattr(app.state, "stdio_clients_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state.stdio_clients_lock = lock
    async with lock:
        existing = clients.get(service_key)
        if existing is not None and existing[0] == signature:
            existing[1].notification_callback = _downstream_notification_callback(
                app,
                service_id=service_key,
                source="stdio",
            )
            return existing[1]
        if existing is not None:
            clients.pop(service_key, None)
            await existing[1].aclose()

        await _ensure_stdio_client_capacity_locked(app, service_key=service_key, clients=clients)

        command = [str(signature[0]), *list(signature[1])]
        client = StdioMcpClient(
            command,
            cwd=signature[3],
            env=dict(signature[2]),
            timeout=float(app.state.settings.downstream_timeout_seconds),
            idle_timeout_seconds=int(signature[4]),
            max_response_bytes=app.state.settings.downstream_max_response_bytes,
        )
        client.notification_callback = _downstream_notification_callback(
            app,
            service_id=service_key,
            source="stdio",
        )
        clients[service_key] = (signature, client)
        return client


def _oauth_error_response(exc: OAuthError) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if exc.retry_after_seconds is not None:
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return JSONResponse(
        {"error": exc.code, "error_description": str(exc)},
        status_code=exc.status_code,
        headers=headers,
    )


def _check_oauth_dcr_rate_limit(request: Request) -> OAuthError | None:
    decision = request.app.state.oauth_dcr_rate_limiter.check(
        f"oauth:dcr:{request_ip(request) or 'unknown'}",
        limit=OAUTH_DCR_RATE_LIMIT,
        window_seconds=OAUTH_DCR_RATE_LIMIT_WINDOW_SECONDS,
    )
    if decision.allowed:
        return None
    return OAuthError(
        "rate_limited",
        "OAuth dynamic client registration rate limit exceeded",
        status_code=429,
        retry_after_seconds=decision.retry_after_seconds,
    )


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
    credential = await app.state.repository.get_service_credential(service_id)
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


def _downstream_session_key(service_id: str | None) -> str:
    return str(service_id or DEFAULT_DOWNSTREAM_SESSION_KEY)


def _downstream_session_id(app: FastAPI, service_id: str | None) -> str | None:
    sessions = getattr(app.state, "downstream_sessions", {})
    session_id = sessions.get(_downstream_session_key(service_id))
    return session_id if isinstance(session_id, str) and session_id else None


def _downstream_session_callback(app: FastAPI, service_id: str | None):
    async def callback(session_id: str) -> None:
        cleaned = session_id.strip()
        if not cleaned:
            return
        app.state.downstream_sessions[_downstream_session_key(service_id)] = cleaned

    return callback


def _forget_downstream_session(app: FastAPI, service_id: str | None) -> None:
    sessions = getattr(app.state, "downstream_sessions", None)
    if isinstance(sessions, dict):
        sessions.pop(_downstream_session_key(service_id), None)


def _capability_present(capabilities: dict[str, Any], key: str) -> bool:
    value = capabilities.get(key)
    return isinstance(value, dict)


def _summary_supports(summary: dict[str, Any], *keys: str) -> bool:
    catalog = summary.get("resource_prompt_catalog") if isinstance(summary.get("resource_prompt_catalog"), dict) else {}
    return any(bool(catalog.get(key)) for key in keys)


async def _server_capabilities_for_default_toolbox(app: FastAPI) -> dict[str, Any]:
    items = [
        item
        for item in await app.state.repository.list_toolbox_items(DEFAULT_TOOLBOX_ID)
        if bool(item.get("enabled")) and item.get("service_status") == "active"
    ]
    if not items:
        return dict(SERVER_CAPABILITIES)

    capabilities: dict[str, Any] = {"tools": {"listChanged": True}}
    resources_supported = False
    prompts_supported = False
    for item in items:
        service = await app.state.repository.get_mcp_service(str(item.get("service_id") or ""))
        if not service:
            continue
        downstream_capabilities = service.get("capabilities_json") if isinstance(service.get("capabilities_json"), dict) else {}
        summary = service.get("validation_summary") if isinstance(service.get("validation_summary"), dict) else {}
        resources_supported = resources_supported or _capability_present(downstream_capabilities, "resources") or _summary_supports(
            summary,
            "resources_supported",
            "resource_templates_supported",
            "resources_found",
            "resource_templates_found",
        )
        prompts_supported = prompts_supported or _capability_present(downstream_capabilities, "prompts") or _summary_supports(
            summary,
            "prompts_supported",
            "prompts_found",
        )

    if resources_supported:
        capabilities["resources"] = {"listChanged": True, "subscribe": False}
    if prompts_supported:
        capabilities["prompts"] = {"listChanged": True}
    return capabilities


def _validate_tool_arguments(schema: Any, arguments: Any) -> str | None:
    if not isinstance(schema, dict):
        return None
    try:
        validator_cls = validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)
        error = next(validator.iter_errors(arguments), None)
    except SchemaError:
        # Downstream supplied an invalid schema. Do not block the call on a
        # malformed catalog entry; validation/service refresh will surface it.
        return None
    except ValidationError as exc:
        error = exc
    if error is None:
        return None
    path = ".".join(str(part) for part in error.absolute_path)
    prefix = f"{path}: " if path else ""
    return f"{prefix}{error.message}"


async def _publish_list_changed(
    app: FastAPI,
    *,
    reason: str,
    resource_id: str | None = None,
    categories: tuple[ListChangedCategory, ...] = LIST_CHANGED_CATEGORIES,
) -> None:
    _invalidate_catalog_caches(app)
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
    if not isinstance(method, str) or method not in DOWNSTREAM_NOTIFICATION_METHODS:
        return
    params = notification.get("params")
    safe_params = params if isinstance(params, dict) else {}
    metadata: dict[str, Any] = {"source": source}
    if service_id:
        metadata["service_id"] = service_id
    if category := LIST_CHANGED_METHOD_CATEGORIES.get(method):
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


def _tool_schema_diff(existing_tools: list[dict[str, Any]], normalized_tools: list[dict[str, Any]]) -> dict[str, Any]:
    existing_by_name = {str(tool.get("original_name")): tool for tool in existing_tools}
    normalized_by_name = {str(tool.get("original_name")): tool for tool in normalized_tools}
    added_names = sorted(set(normalized_by_name) - set(existing_by_name))
    removed_names = sorted(set(existing_by_name) - set(normalized_by_name))
    changed_tools = []
    for name in sorted(set(existing_by_name) & set(normalized_by_name)):
        previous_hash = existing_by_name.get(name, {}).get("schema_hash")
        current_hash = normalized_by_name.get(name, {}).get("schema_hash")
        if previous_hash != current_hash:
            changed_tools.append(
                {
                    "name": name,
                    "previous_schema_hash": previous_hash,
                    "current_schema_hash": current_hash,
                }
            )
    summary = {
        "previous_tool_count": len(existing_tools),
        "discovered_tool_count": len(normalized_tools),
        "changed_tool_count": len(changed_tools) + len(added_names) + len(removed_names),
        "added_tool_count": len(added_names),
        "removed_tool_count": len(removed_names),
    }
    details = {
        "added": [
            {
                "name": name,
                "schema_hash": normalized_by_name.get(name, {}).get("schema_hash"),
            }
            for name in added_names
        ],
        "removed": [
            {
                "name": name,
                "schema_hash": existing_by_name.get(name, {}).get("schema_hash"),
            }
            for name in removed_names
        ],
        "changed": changed_tools,
    }
    return {"summary": summary, "details": details}


def _tool_schema_change_summary(existing_tools: list[dict[str, Any]], normalized_tools: list[dict[str, Any]]) -> dict[str, int]:
    return _tool_schema_diff(existing_tools, normalized_tools)["summary"]


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
            await app.state.repository.log_audit(
                action="downstream.cancel.forward",
                resource_type="mcp_service",
                resource_id=inflight.get("service_id"),
                metadata={"request_id": str(cancelled_request_id), "transport_type": "stdio"},
                request_id=correlation_id(request),
                ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        except (TimeoutError, DownstreamMcpError) as exc:
            await app.state.repository.log_audit(
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
            await app.state.repository.log_audit(
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
        await app.state.repository.log_audit(
            action="downstream.cancel.forward",
            resource_type="mcp_service",
            resource_id=inflight.get("service_id") if isinstance(inflight, dict) else None,
            metadata={"request_id": str(cancelled_request_id), "has_inflight_route": bool(target_url)},
            request_id=correlation_id(request),
            ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except TimeoutError:
        await app.state.repository.log_audit(
            action="downstream.cancel.forward_failed",
            resource_type="mcp_service",
            resource_id=inflight.get("service_id") if isinstance(inflight, dict) else None,
            metadata={"request_id": str(cancelled_request_id), "reason": "timeout"},
            request_id=correlation_id(request),
            ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except DownstreamMcpError as exc:
        await app.state.repository.log_audit(
            action="downstream.cancel.forward_failed",
            resource_type="mcp_service",
            resource_id=inflight.get("service_id") if isinstance(inflight, dict) else None,
            metadata={"request_id": str(cancelled_request_id), "reason": str(exc), "code": exc.code},
            request_id=correlation_id(request),
            ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )


async def _refresh_tools(
    app: FastAPI,
    *,
    request_id: Any,
    protocol_version: str | None,
    session_id: str | None,
    params: dict[str, Any] | None = None,
    correlation_id_value: str | None = None,
) -> dict[str, Any]:
    repository: Repository = app.state.repository
    catalog_rows = await repository.get_catalog_tools(DEFAULT_TOOLBOX_ID)
    if catalog_rows:
        registry: dict[str, dict[str, Any]] = {}
        tools: list[dict[str, Any]] = []
        for row in catalog_rows:
            tool = catalog_row_to_mcp_tool(row)
            registry[tool["name"]] = {
                "original_name": row["original_name"],
                "endpoint_url": row["endpoint_url"],
                "transport_type": row.get("transport_type") or "http",
                "stdio_command": row.get("stdio_command"),
                "stdio_args": row.get("stdio_args") or [],
                "stdio_env": row.get("stdio_env") or {},
                "stdio_cwd": row.get("stdio_cwd"),
                "stdio_idle_timeout_seconds": row.get("stdio_idle_timeout_seconds"),
                "input_schema_json": row.get("input_schema_json") or {"type": "object"},
                "schema_hash": row.get("schema_hash"),
                "service_id": row["service_id"],
                "service_tool_id": row["service_tool_id"],
                "override_enabled": row.get("override_enabled", 1),
                "permission_level": row.get("permission_level", "callable"),
            }
            if bool(row.get("override_enabled", 1)) and row.get("permission_level", "callable") != "hidden":
                tools.append(tool)
        app.state.tool_registry = registry
        result_payload: dict[str, Any] = {"tools": tools, "nextCursor": None}
        unavailable = await _toolbox_unavailable_services(app, DEFAULT_TOOLBOX_ID)
        if unavailable:
            result_payload["_meta"] = {"coremcp": {"unavailable_services": unavailable}}
        return result_payload

    downstream: DownstreamMcpClient = app.state.downstream
    response = await downstream.request(
        method="tools/list",
        params=params or {},
        request_id=request_id,
        protocol_version=protocol_version,
        session_id=_downstream_session_id(app, None),
        correlation_id=correlation_id_value,
        session_id_callback=_downstream_session_callback(app, None),
        notification_callback=_downstream_notification_callback(app, source="http"),
    )
    result = response.get("result")
    if not isinstance(result, dict):
        raise DownstreamMcpError("downstream tools/list returned invalid result")

    transformed_tools: list[dict[str, Any]] = []
    registry = {}
    for tool in result.get("tools", []):
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        transformed, original_name = _normalize_downstream_tool(tool)
        transformed_tools.append(transformed)
        registry[transformed["name"]] = {
            "original_name": original_name,
            "endpoint_url": app.state.settings.fake_mcp_url,
            "transport_type": "http",
            "service_id": None,
            "service_tool_id": None,
            "input_schema_json": tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object"},
        }

    app.state.tool_registry = registry
    result = dict(result)
    result["tools"] = transformed_tools
    result.setdefault("nextCursor", None)
    return result


async def _active_toolbox_services(app: FastAPI, toolbox_id: str = DEFAULT_TOOLBOX_ID) -> list[dict[str, Any]]:
    services: list[dict[str, Any]] = []
    for item in await app.state.repository.list_toolbox_items(toolbox_id):
        if not bool(item.get("enabled")) or item.get("service_status") != "active":
            continue
        service = await app.state.repository.get_mcp_service(str(item["service_id"]))
        if service and service.get("status") == "active":
            services.append(service)
    return services


async def _toolbox_unavailable_services(app: FastAPI, toolbox_id: str = DEFAULT_TOOLBOX_ID) -> list[dict[str, Any]]:
    unavailable: list[dict[str, Any]] = []
    for item in await app.state.repository.list_toolbox_items(toolbox_id):
        if not bool(item.get("enabled")):
            continue
        service_id = str(item.get("service_id") or "")
        service_status = str(item.get("service_status") or "unknown")
        if service_status != "active":
            unavailable.append(
                {
                    "service_id": service_id,
                    "service_slug": item.get("service_slug"),
                    "status": service_status,
                    "reason": "service_not_active",
                }
            )
            continue
        if service_id:
            snapshot = app.state.circuit_breaker.snapshot(service_id)
            if snapshot.state == "open":
                unavailable.append(
                    {
                        "service_id": service_id,
                        "service_slug": item.get("service_slug"),
                        "status": "circuit_open",
                        "reason": "circuit_open",
                        "retry_after_seconds": snapshot.retry_after_seconds,
                    }
                )
    return unavailable


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
) -> dict[str, Any]:
    service_id = str(service.get("id") or service.get("service_id") or "")
    if _transport_type(service) == "stdio":
        client = await _stdio_client_for_config(app, service)
        return await client.request(
            method=method,
            params=params or {},
            request_id=request_id,
            protocol_version=protocol_version,
            session_id=session_id,
            correlation_id=correlation_id_value,
        )

    checker = UrlSafetyChecker(app.state.settings)
    safety_result = checker.assert_safe(service["endpoint_url"])
    downstream_session_id = _downstream_session_id(app, service_id)
    return await app.state.downstream.request(
        method=method,
        params=params or {},
        request_id=request_id,
        protocol_version=protocol_version,
        session_id=downstream_session_id,
        url=service["endpoint_url"],
        downstream_headers=await _downstream_headers_for_service(app, service_id),
        url_safety_checker=checker,
        safety_result=safety_result,
        correlation_id=correlation_id_value,
        session_id_callback=_downstream_session_callback(app, service_id),
        notification_callback=_downstream_notification_callback(app, service_id=service_id, source="http"),
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
) -> dict[str, Any]:
    return await app.state.downstream.request(
        method=method,
        params=params or {},
        request_id=request_id,
        protocol_version=protocol_version,
        session_id=_downstream_session_id(app, None),
        correlation_id=correlation_id_value,
        session_id_callback=_downstream_session_callback(app, None),
        notification_callback=_downstream_notification_callback(app, source="http"),
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


def _cached_resource_to_mcp(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    item = dict(metadata)
    item["uri"] = row["uri"]
    if row.get("name"):
        item["name"] = row["name"]
    if row.get("title"):
        item["title"] = row["title"]
    if row.get("description"):
        item["description"] = row["description"]
    if row.get("mime_type"):
        item["mimeType"] = row["mime_type"]
    if isinstance(row.get("annotations"), dict) and row["annotations"]:
        item["annotations"] = row["annotations"]
    return item


def _unambiguous_resource_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        uri = str(row.get("uri") or "")
        if uri:
            counts[uri] = counts.get(uri, 0) + 1
    return [row for row in rows if counts.get(str(row.get("uri") or ""), 0) == 1]


def _cached_resource_template_to_mcp(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    item = dict(metadata)
    item["uriTemplate"] = row["uri_template"]
    if row.get("name"):
        item["name"] = row["name"]
    if row.get("title"):
        item["title"] = row["title"]
    if row.get("description"):
        item["description"] = row["description"]
    if row.get("mime_type"):
        item["mimeType"] = row["mime_type"]
    if isinstance(row.get("annotations"), dict) and row["annotations"]:
        item["annotations"] = row["annotations"]
    return item


def _cached_prompt_to_mcp(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    item = dict(metadata)
    item["name"] = f"{row['service_slug']}.{row['name']}"
    if row.get("title"):
        item["title"] = row["title"]
    if row.get("description"):
        item["description"] = row["description"]
    if isinstance(row.get("arguments_json"), list):
        item["arguments"] = row["arguments_json"]
    return item


def _resource_content_meta(
    *,
    kind: str,
    original_length: int,
    max_length: int,
) -> dict[str, Any]:
    return {
        "truncated": True,
        "kind": kind,
        "originalLength": original_length,
        "maxLength": max_length,
        "reason": "resource_content_too_large",
    }


def _truncate_resource_read_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep `resources/read` responses usable for LLM clients.

    MCP resources can be large files or binary blobs. CoreMCP remains a
    personal gateway, so we do not store or inspect the full content, but we do
    cap oversized payloads before returning them to clients to prevent one
    resource from flooding Codex/Claude context windows.
    """

    contents = result.get("contents")
    if not isinstance(contents, list):
        return result

    truncated_any = False
    normalized_contents: list[Any] = []
    for item in contents:
        if not isinstance(item, dict):
            normalized_contents.append(item)
            continue

        normalized = dict(item)
        item_meta = dict(normalized.get("_meta") or {}) if isinstance(normalized.get("_meta"), dict) else {}

        text = normalized.get("text")
        if isinstance(text, str) and len(text) > RESOURCE_READ_MAX_TEXT_CHARS:
            normalized["text"] = text[:RESOURCE_READ_MAX_TEXT_CHARS] + "\n…[CoreMCP truncated oversized resource text]"
            item_meta["coremcp"] = _resource_content_meta(
                kind="text",
                original_length=len(text),
                max_length=RESOURCE_READ_MAX_TEXT_CHARS,
            )
            truncated_any = True

        blob = normalized.get("blob")
        if isinstance(blob, str) and len(blob) > RESOURCE_READ_MAX_BLOB_CHARS:
            normalized["blob"] = blob[:RESOURCE_READ_MAX_BLOB_CHARS]
            item_meta["coremcp"] = _resource_content_meta(
                kind="blob",
                original_length=len(blob),
                max_length=RESOURCE_READ_MAX_BLOB_CHARS,
            )
            truncated_any = True

        if item_meta:
            normalized["_meta"] = item_meta
        normalized_contents.append(normalized)

    if not truncated_any:
        return result

    normalized_result = dict(result)
    normalized_result["contents"] = normalized_contents
    result_meta = dict(normalized_result.get("_meta") or {}) if isinstance(normalized_result.get("_meta"), dict) else {}
    result_meta["coremcp"] = {
        "truncated": True,
        "reason": "resource_content_too_large",
        "maxTextChars": RESOURCE_READ_MAX_TEXT_CHARS,
        "maxBlobChars": RESOURCE_READ_MAX_BLOB_CHARS,
    }
    normalized_result["_meta"] = result_meta
    return normalized_result


async def _persist_stdio_state(app: FastAPI, service_id: str | None, client: StdioMcpClient | None) -> None:
    if not service_id or client is None:
        return
    snapshot = client.snapshot()
    await app.state.repository.update_mcp_service(
        service_id,
        {
            "last_stdio_started_at": snapshot.get("started_at"),
            "last_stdio_used_at": snapshot.get("last_used_at"),
            "stdio_restart_count": int(snapshot.get("restart_count") or 0),
            "last_stdio_exit_code": snapshot.get("last_exit_code"),
            "last_stdio_error": snapshot.get("last_error"),
            "last_stdio_stderr_tail": snapshot.get("stderr_tail"),
        },
    )


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
        return await app.state.repository.mark_stuck_jobs_failed(max_age_seconds=JOB_REAP_MAX_AGE_SECONDS)

    await run_reaper_loop(
        interval_seconds=INFLIGHT_REAP_INTERVAL_SECONDS,
        session_reap=session_reap,
        inflight_reap=inflight_reap,
        stuck_job_cleanup=stuck_job_cleanup,
        run_immediately=False,
        on_error=lambda exc: None,
    )


async def _detect_service_tool_schema_drift(
    app: FastAPI,
    service: dict[str, Any],
    *,
    protocol_version: str,
    timeout: httpx.Timeout,
) -> bool:
    service_id = str(service.get("id") or "")
    if not service_id:
        return False
    if _transport_type(service) == "stdio":
        stdio_client = await _stdio_client_for_config(app, service)
        response = await stdio_client.request(
            method="tools/list",
            params={},
            request_id=f"health-{service_id}-tools",
            protocol_version=protocol_version,
            correlation_id=f"health-probe-{service_id}",
        )
    else:
        checker = UrlSafetyChecker(app.state.settings)
        safety_result = checker.assert_safe(str(service.get("endpoint_url") or ""))
        downstream_headers = await _downstream_headers_for_service(app, service_id)
        response = await app.state.downstream.request(
            method="tools/list",
            params={},
            request_id=f"health-{service_id}-tools",
            protocol_version=protocol_version,
            session_id=_downstream_session_id(app, service_id),
            url=str(service.get("endpoint_url") or ""),
            downstream_headers=downstream_headers,
            url_safety_checker=checker,
            safety_result=safety_result,
            correlation_id=f"health-probe-{service_id}",
            timeout=timeout,
            session_id_callback=_downstream_session_callback(app, service_id),
            notification_callback=_downstream_notification_callback(app, service_id=service_id, source="http"),
        )
    result = response.get("result")
    tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        return False
    existing_tools = await app.state.repository.list_service_tools(service_id)
    normalized, _warnings = normalize_downstream_tools(
        tools,
        service_slug=str(service.get("slug") or service_id),
        settings=app.state.settings,
    )
    return _tool_schema_change_summary(existing_tools, normalized).get("changed_tool_count", 0) > 0


async def _probe_service_health(app: FastAPI, service: dict[str, Any]) -> tuple[bool, str | None]:
    service_id = str(service.get("id") or "")
    if not service_id:
        return False, "missing service id"
    protocol_version = str(service.get("protocol_version") or "2025-11-25")
    timeout_seconds = max(0.1, float(app.state.settings.service_health_probe_timeout_seconds))
    timeout = httpx.Timeout(
        timeout_seconds,
        connect=min(float(app.state.settings.downstream_connect_timeout_seconds), timeout_seconds),
        read=timeout_seconds,
        write=timeout_seconds,
        pool=timeout_seconds,
    )
    try:
        if _transport_type(service) == "stdio":
            stdio_client = await _stdio_client_for_config(app, service)
            await stdio_client.request(
                method="initialize",
                params={"protocolVersion": protocol_version, "capabilities": {}, "clientInfo": {"name": "coremcp-health-probe", "version": app.state.settings.app_version}},
                request_id=f"health-{service_id}",
                protocol_version=protocol_version,
                correlation_id=f"health-probe-{service_id}",
            )
            await _persist_stdio_state(app, service_id, stdio_client)
        else:
            checker = UrlSafetyChecker(app.state.settings)
            safety_result = checker.assert_safe(str(service.get("endpoint_url") or ""))
            downstream_headers = await _downstream_headers_for_service(app, service_id)
            await app.state.downstream.request(
                method="initialize",
                params={"protocolVersion": protocol_version, "capabilities": {}, "clientInfo": {"name": "coremcp-health-probe", "version": app.state.settings.app_version}},
                request_id=f"health-{service_id}",
                protocol_version=protocol_version,
                url=str(service.get("endpoint_url") or ""),
                downstream_headers=downstream_headers,
                url_safety_checker=checker,
                safety_result=safety_result,
                correlation_id=f"health-probe-{service_id}",
                timeout=timeout,
                session_id_callback=_downstream_session_callback(app, service_id),
                notification_callback=_downstream_notification_callback(app, service_id=service_id, source="http"),
            )
        if await _detect_service_tool_schema_drift(
            app,
            service,
            protocol_version=protocol_version,
            timeout=timeout,
        ):
            await validate_service(app, service_id, correlation_id_value=f"health-drift-{service_id}")
        app.state.circuit_breaker.record_success(service_id)
        await app.state.repository.mark_service_health_probe(service_id=service_id, ok=True)
        return True, None
    except Exception as exc:  # noqa: BLE001 - health probes must isolate failing services.
        app.state.circuit_breaker.record_failure(service_id)
        await app.state.repository.mark_service_health_probe(
            service_id=service_id,
            ok=False,
            error_message=str(exc),
            circuit_open_seconds=SERVICE_HEALTH_CIRCUIT_OPEN_SECONDS,
            failure_threshold=SERVICE_HEALTH_FAILURE_THRESHOLD,
        )
        return False, str(exc)


async def _run_service_health_probe_once(app: FastAPI) -> dict[str, Any]:
    services = await app.state.repository.list_mcp_services(limit=500)
    candidates = [
        service
        for service in services
        if str(service.get("status") or "") in {"active", "error", "auth_required", "validating"}
    ]
    checked = 0
    failed = 0
    for service in candidates:
        checked += 1
        ok, _error = await _probe_service_health(app, service)
        if not ok:
            failed += 1
    return {"checked": checked, "failed": failed}


async def _run_service_health_probe_loop(app: FastAPI) -> None:
    interval = max(5.0, float(app.state.settings.service_health_probe_interval_seconds))
    while True:
        await asyncio.sleep(interval)
        try:
            await _run_service_health_probe_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            # This loop is best-effort observability; API serving must continue.
            continue


async def _handle_initialize(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
) -> tuple[dict[str, Any], str]:
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    requested_raw = params.get("protocolVersion") or request.headers.get("MCP-Protocol-Version")
    requested = str(requested_raw) if requested_raw is not None else None
    protocol_version = negotiate_protocol_version(requested)
    session = app.state.sessions.create(protocol_version)

    downstream_params = dict(params)
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


async def _handle_tools_list(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    started = time.perf_counter()
    session_id = request.headers.get("Mcp-Session-Id")
    session = app.state.sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    request_log_id = correlation_id(request)
    try:
        result = await _refresh_tools(
            app,
            request_id=_get_request_id(payload),
            protocol_version=protocol_version,
            session_id=session_id,
            params=payload.get("params") if isinstance(payload.get("params"), dict) else {},
            correlation_id_value=correlation_id(request),
        )
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/list",
            tool_name=None,
            status="success",
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_log_id,
            protocol_version=protocol_version,
            client_ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return jsonrpc_result(_get_request_id(payload), result)
    except DownstreamMcpError as exc:
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/list",
            tool_name=None,
            status="error",
            error_code=exc.code,
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_log_id,
            error_message=str(exc),
            protocol_version=protocol_version,
            client_ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return jsonrpc_error(_get_request_id(payload), exc.code, str(exc))


async def _handle_resources_list(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
    *,
    method: str = "resources/list",
    result_key: str = "resources",
) -> dict[str, Any]:
    request_id = _get_request_id(payload)
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    session_id = request.headers.get("Mcp-Session-Id")
    session = app.state.sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    if method == "resources/list":
        cached = await app.state.repository.list_catalog_resources(DEFAULT_TOOLBOX_ID)
        if cached:
            return jsonrpc_result(
                request_id,
                {
                    "resources": [
                        _cached_resource_to_mcp(row)
                        for row in _unambiguous_resource_rows(cached)
                    ],
                    "nextCursor": None,
                },
            )
    elif method == "resources/templates/list":
        cached_templates = await app.state.repository.list_catalog_resource_templates(DEFAULT_TOOLBOX_ID)
        if cached_templates:
            return jsonrpc_result(
                request_id,
                {"resourceTemplates": [_cached_resource_template_to_mcp(row) for row in cached_templates], "nextCursor": None},
            )
    services = await _active_toolbox_services(app)
    if not services:
        try:
            response = await _request_default_downstream_rpc(
                app,
                method=method,
                params=params,
                request_id=request_id,
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=correlation_id(request),
            )
            result = response.get("result")
            return jsonrpc_result(request_id, result if isinstance(result, dict) else {result_key: [], "nextCursor": None})
        except DownstreamMcpError as exc:
            return jsonrpc_error(request_id, exc.code, str(exc))

    merged: list[dict[str, Any]] = []
    for service in services:
        try:
            response = await _request_service_rpc(
                app,
                service,
                method=method,
                params=params,
                request_id=f"{request_id}-{service['id']}-{method}",
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=correlation_id(request),
            )
        except DownstreamMcpError as exc:
            if exc.code == -32601:
                continue
            continue
        result = response.get("result")
        items = result.get(result_key) if isinstance(result, dict) else None
        if isinstance(items, list):
            merged.extend(item for item in items if isinstance(item, dict))
    return jsonrpc_result(request_id, {result_key: merged, "nextCursor": None})


async def _handle_resources_read(app: FastAPI, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    request_id = _get_request_id(payload)
    params = payload.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("uri"), str):
        return jsonrpc_error(request_id, -32602, "Invalid params")
    session_id = request.headers.get("Mcp-Session-Id")
    session = app.state.sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    cached = await app.state.repository.get_catalog_resource_by_uri(str(params["uri"]), DEFAULT_TOOLBOX_ID)
    if cached is not None:
        try:
            response = await _request_service_rpc(
                app,
                _service_config_from_catalog_row(cached),
                method="resources/read",
                params=params,
                request_id=f"{request_id}-{cached['service_id']}-resource-read",
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=correlation_id(request),
            )
            result = response.get("result")
            return jsonrpc_result(request_id, _truncate_resource_read_result(result) if isinstance(result, dict) else {})
        except DownstreamMcpError as exc:
            return jsonrpc_error(request_id, exc.code, str(exc))
    services = await _active_toolbox_services(app)
    if not services:
        try:
            response = await _request_default_downstream_rpc(
                app,
                method="resources/read",
                params=params,
                request_id=request_id,
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=correlation_id(request),
            )
            result = response.get("result")
            return jsonrpc_result(request_id, _truncate_resource_read_result(result) if isinstance(result, dict) else {})
        except DownstreamMcpError as exc:
            return jsonrpc_error(request_id, exc.code, str(exc))
    return jsonrpc_error(request_id, -32602, "Unknown resource")


async def _handle_prompts_list(app: FastAPI, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    request_id = _get_request_id(payload)
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    session_id = request.headers.get("Mcp-Session-Id")
    session = app.state.sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    cached_prompts = await app.state.repository.list_catalog_prompts(DEFAULT_TOOLBOX_ID)
    if cached_prompts:
        return jsonrpc_result(request_id, {"prompts": [_cached_prompt_to_mcp(row) for row in cached_prompts], "nextCursor": None})
    services = await _active_toolbox_services(app)
    if not services:
        try:
            response = await _request_default_downstream_rpc(
                app,
                method="prompts/list",
                params=params,
                request_id=request_id,
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=correlation_id(request),
            )
            result = response.get("result")
            return jsonrpc_result(request_id, result if isinstance(result, dict) else {"prompts": [], "nextCursor": None})
        except DownstreamMcpError as exc:
            return jsonrpc_error(request_id, exc.code, str(exc))

    prompts: list[dict[str, Any]] = []
    for service in services:
        try:
            response = await _request_service_rpc(
                app,
                service,
                method="prompts/list",
                params=params,
                request_id=f"{request_id}-{service['id']}-prompts-list",
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=correlation_id(request),
            )
        except DownstreamMcpError as exc:
            if exc.code == -32601:
                continue
            continue
        result = response.get("result")
        items = result.get("prompts") if isinstance(result, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            prompt = dict(item)
            prompt["name"] = f"{service['slug']}.{item['name']}"
            prompts.append(prompt)
    return jsonrpc_result(request_id, {"prompts": prompts, "nextCursor": None})


async def _handle_prompts_get(app: FastAPI, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    request_id = _get_request_id(payload)
    params = payload.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        return jsonrpc_error(request_id, -32602, "Invalid params")
    session_id = request.headers.get("Mcp-Session-Id")
    session = app.state.sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    requested_name = str(params["name"])
    cached = await app.state.repository.get_catalog_prompt_by_exposed_name(requested_name, DEFAULT_TOOLBOX_ID)
    if cached is not None:
        downstream_params = dict(params)
        downstream_params["name"] = cached["name"]
        try:
            response = await _request_service_rpc(
                app,
                _service_config_from_catalog_row(cached),
                method="prompts/get",
                params=downstream_params,
                request_id=f"{request_id}-{cached['service_id']}-prompts-get",
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=correlation_id(request),
            )
            result = response.get("result")
            return jsonrpc_result(request_id, result if isinstance(result, dict) else {})
        except DownstreamMcpError as exc:
            return jsonrpc_error(request_id, exc.code, str(exc))
    services = await _active_toolbox_services(app)
    if not services:
        try:
            response = await _request_default_downstream_rpc(
                app,
                method="prompts/get",
                params=params,
                request_id=request_id,
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=correlation_id(request),
            )
            result = response.get("result")
            return jsonrpc_result(request_id, result if isinstance(result, dict) else {})
        except DownstreamMcpError as exc:
            return jsonrpc_error(request_id, exc.code, str(exc))

    candidates: list[tuple[dict[str, Any], str]] = []
    if "." in requested_name:
        service_slug, original_name = requested_name.split(".", 1)
        candidates = [(service, original_name) for service in services if service.get("slug") == service_slug]
    else:
        candidates = [(service, requested_name) for service in services]

    last_error: DownstreamMcpError | None = None
    for service, original_name in candidates:
        downstream_params = dict(params)
        downstream_params["name"] = original_name
        try:
            response = await _request_service_rpc(
                app,
                service,
                method="prompts/get",
                params=downstream_params,
                request_id=f"{request_id}-{service['id']}-prompts-get",
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=correlation_id(request),
            )
            result = response.get("result")
            if isinstance(result, dict):
                return jsonrpc_result(request_id, result)
        except DownstreamMcpError as exc:
            last_error = exc
            continue
    if last_error is not None and last_error.code not in {-32601, -32602}:
        return jsonrpc_error(request_id, last_error.code, str(last_error))
    return jsonrpc_error(request_id, -32602, "Unknown prompt")


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
    saved_resources = await app.state.repository.replace_service_resources(str(service["id"]), resources)
    summary["resources_found"] = len(saved_resources)

    templates = await request_list("resources/templates/list", "resourceTemplates")
    if templates:
        summary["resource_templates_supported"] = True
    saved_templates = await app.state.repository.replace_service_resource_templates(str(service["id"]), templates)
    summary["resource_templates_found"] = len(saved_templates)

    prompts = await request_list("prompts/list", "prompts")
    if prompts:
        summary["prompts_supported"] = True
    saved_prompts = await app.state.repository.replace_service_prompts(str(service["id"]), prompts)
    summary["prompts_found"] = len(saved_prompts)

    return summary


async def _handle_tools_call(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    started = time.perf_counter()
    request_id = _get_request_id(payload)
    params = payload.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        return jsonrpc_error(request_id, -32602, "Invalid params")

    session_id = request.headers.get("Mcp-Session-Id")
    session = app.state.sessions.get(session_id)
    protocol_version = session.protocol_version if session else request.headers.get("MCP-Protocol-Version")
    request_log_id = correlation_id(request)
    exposed_name = params["name"]
    idempotency_key = _idempotency_cache_key(request, exposed_name)
    cached_response = app.state.idempotency_cache.get(idempotency_key)
    if cached_response is not None:
        # Safe to mutate: IdempotencyCache.get() returns a deepcopy.
        cached_response["id"] = request_id
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/call",
            tool_name=exposed_name,
            status="success",
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_log_id,
            protocol_version=protocol_version,
            idempotency_key=request.headers.get("Idempotency-Key"),
            client_ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return cached_response

    registry: dict[str, dict[str, Any]] = getattr(app.state, "tool_registry", {})
    if exposed_name not in registry:
        try:
            await _refresh_tools(
                app,
                request_id=request_id,
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id_value=correlation_id(request),
            )
            registry = app.state.tool_registry
        except DownstreamMcpError as exc:
            await app.state.repository.log_invocation(
                session_id=session_id,
                method="tools/call",
                tool_name=exposed_name,
                status="error",
                error_code=exc.code,
                latency_ms=int((time.perf_counter() - started) * 1000),
                request_id=request_log_id,
                error_message=str(exc),
                protocol_version=protocol_version,
                client_ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            return jsonrpc_error(request_id, exc.code, str(exc))

    route = registry.get(exposed_name)
    if route is None:
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/call",
            tool_name=exposed_name,
            status="error",
            error_code=-32602,
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_log_id,
            error_message="Unknown tool",
            protocol_version=protocol_version,
            client_ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return jsonrpc_error(request_id, -32602, "Unknown tool")

    permission_level = str(route.get("permission_level") or "callable")
    override_enabled = bool(route.get("override_enabled", 1))
    if not override_enabled or permission_level != "callable":
        reason = "tool_disabled" if not override_enabled or permission_level == "hidden" else f"tool_permission_{permission_level}"
        await app.state.repository.log_audit(
            action="policy.deny",
            resource_type="service_tool",
            resource_id=route.get("service_tool_id"),
            metadata={"tool": exposed_name, "reason": reason, "permission_level": permission_level, "enabled": override_enabled},
            request_id=request_log_id,
            ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/call",
            tool_name=exposed_name,
            status="policy_denied",
            error_code="tool_permission_denied",
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_log_id,
            service_id=route.get("service_id"),
            service_tool_id=route.get("service_tool_id"),
            downstream_tool_name=route.get("original_name"),
            error_message=reason,
            protocol_version=protocol_version,
            client_ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return jsonrpc_result(
            request_id,
            tool_error_result(
                "policy_denied",
                f"Tool call denied by CoreMCP toolbox policy: {reason}",
                reason=reason,
            ),
        )

    arguments = params.get("arguments", {})
    schema_error = _validate_tool_arguments(route.get("input_schema_json"), arguments)
    if schema_error is not None:
        await app.state.repository.log_audit(
            action="policy.invalid_args",
            resource_type="service_tool",
            resource_id=route.get("service_tool_id"),
            metadata={"tool": exposed_name, "error": schema_error, "schema_hash": route.get("schema_hash")},
            request_id=request_log_id,
            ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/call",
            tool_name=exposed_name,
            status="policy_denied",
            error_code="invalid_args",
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_log_id,
            service_id=route.get("service_id"),
            service_tool_id=route.get("service_tool_id"),
            downstream_tool_name=route.get("original_name"),
            error_message=schema_error,
            protocol_version=protocol_version,
            client_ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return jsonrpc_error(
            request_id,
            -32602,
            "Invalid tool arguments",
            {"details": schema_error},
        )

    downstream_params = dict(params)
    downstream_params["name"] = route["original_name"]
    service_id = str(route.get("service_id") or "")
    if service_id:
        if decision := _check_service_rate_limit(
            app,
            service_id=service_id,
            method="tools/call",
            tool_name=exposed_name,
        ):
            await app.state.repository.log_audit(
                action="rate_limit.exceeded",
                resource_type="mcp_service",
                resource_id=service_id,
                metadata={"tool": exposed_name, "route": "tools/call", "retry_after_seconds": decision.retry_after_seconds},
                request_id=request_log_id,
                ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            await app.state.repository.log_invocation(
                session_id=session_id,
                method="tools/call",
                tool_name=exposed_name,
                status="rate_limited",
                error_code="service_rate_limited",
                latency_ms=int((time.perf_counter() - started) * 1000),
                request_id=request_log_id,
                service_id=route.get("service_id"),
                service_tool_id=route.get("service_tool_id"),
                downstream_tool_name=route.get("original_name"),
                error_message="service_rate_limit_exceeded",
                protocol_version=protocol_version,
                client_ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            return jsonrpc_result(request_id, _rate_limit_tool_error(decision.retry_after_seconds))
        try:
            app.state.circuit_breaker.before_request(service_id)
        except CircuitOpenError as exc:
            await app.state.repository.log_invocation(
                session_id=session_id,
                method="tools/call",
                tool_name=exposed_name,
                status="error",
                error_code="circuit_open",
                latency_ms=int((time.perf_counter() - started) * 1000),
                request_id=request_log_id,
                service_id=route.get("service_id"),
                service_tool_id=route.get("service_tool_id"),
                downstream_tool_name=route.get("original_name"),
                error_message=str(exc),
                protocol_version=protocol_version,
                client_ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            return jsonrpc_result(
                request_id,
                tool_error_result(
                    "circuit_open",
                    "Downstream service circuit is temporarily open",
                    retry_after_seconds=exc.retry_after_seconds,
                ),
            )

    stdio_client_for_call: StdioMcpClient | None = None
    try:
        inflight_key = str(request_id)
        inflight_started_at = time.time()
        inflight_timeout_at = inflight_started_at + float(app.state.settings.downstream_timeout_seconds)
        if _transport_type(route) == "stdio":
            stdio_client = await _stdio_client_for_config(app, route)
            stdio_client_for_call = stdio_client
            app.state.inflight_downstream_calls[inflight_key] = {
                **route,
                "transport_type": "stdio",
                "method": "tools/call",
                "started_at": inflight_started_at,
                "timeout_at": inflight_timeout_at,
                "service_id": route.get("service_id"),
                "session_id": session_id,
                "protocol_version": protocol_version,
            }
            downstream_response = await stdio_client.request(
                method="tools/call",
                params=downstream_params,
                request_id=request_id,
                protocol_version=protocol_version,
                session_id=session_id,
                correlation_id=correlation_id(request),
            )
        else:
            checker = UrlSafetyChecker(app.state.settings)
            safety_result = checker.assert_safe(route["endpoint_url"])
            downstream_headers = await _downstream_headers_for_service(app, route.get("service_id"))
            downstream_headers.update(_idempotency_downstream_header(request))
            downstream_session_id = _downstream_session_id(
                app,
                route.get("service_id") if isinstance(route.get("service_id"), str) else None,
            )
            app.state.inflight_downstream_calls[inflight_key] = {
                "url": route["endpoint_url"],
                "transport_type": "http",
                "method": "tools/call",
                "started_at": inflight_started_at,
                "timeout_at": inflight_timeout_at,
                "service_id": route.get("service_id"),
                "session_id": downstream_session_id,
                "protocol_version": protocol_version,
                "downstream_headers": downstream_headers,
            }
            downstream_response = await app.state.downstream.request(
                method="tools/call",
                params=downstream_params,
                request_id=request_id,
                protocol_version=protocol_version,
                session_id=downstream_session_id,
                url=route["endpoint_url"],
                downstream_headers=downstream_headers,
                url_safety_checker=checker,
                safety_result=safety_result,
                correlation_id=correlation_id(request),
                session_id_callback=_downstream_session_callback(
                    app,
                    route.get("service_id") if isinstance(route.get("service_id"), str) else None,
                ),
                notification_callback=_downstream_notification_callback(
                    app,
                    service_id=route.get("service_id") if isinstance(route.get("service_id"), str) else None,
                    source="http",
                ),
            )
        app.state.inflight_downstream_calls.pop(inflight_key, None)
        result = downstream_response.get("result")
        if not isinstance(result, dict):
            raise DownstreamMcpError("downstream tools/call returned invalid result")
        if service_id:
            app.state.circuit_breaker.record_success(service_id)
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/call",
            tool_name=exposed_name,
            status="success",
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_log_id,
            service_id=route.get("service_id"),
            service_tool_id=route.get("service_tool_id"),
            downstream_tool_name=route.get("original_name"),
            protocol_version=protocol_version,
            idempotency_key=request.headers.get("Idempotency-Key"),
            client_ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        response_payload = jsonrpc_result(request_id, result)
        app.state.idempotency_cache.set(idempotency_key, response_payload)
        return response_payload
    except DownstreamTimeoutError as exc:
        app.state.inflight_downstream_calls.pop(str(request_id), None)
        if service_id:
            app.state.circuit_breaker.record_failure(service_id)
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/call",
            tool_name=exposed_name,
            status="timeout",
            error_code="downstream_timeout",
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_log_id,
            service_id=route.get("service_id"),
            service_tool_id=route.get("service_tool_id"),
            downstream_tool_name=route.get("original_name"),
            error_message=str(exc),
            protocol_version=protocol_version,
            client_ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return jsonrpc_result(
            request_id,
            tool_error_result("downstream_timeout", "Downstream tool call timed out", downstream_code=exc.code),
        )
    except DownstreamToolError as exc:
        app.state.inflight_downstream_calls.pop(str(request_id), None)
        if service_id:
            app.state.circuit_breaker.record_success(service_id)
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/call",
            tool_name=exposed_name,
            status="error",
            error_code=exc.code,
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_log_id,
            service_id=route.get("service_id"),
            service_tool_id=route.get("service_tool_id"),
            downstream_tool_name=route.get("original_name"),
            error_message=str(exc),
            protocol_version=protocol_version,
            client_ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        response_payload = jsonrpc_result(request_id, tool_error_result("downstream_error", str(exc), downstream_code=exc.code))
        app.state.idempotency_cache.set(idempotency_key, response_payload)
        return response_payload
    except DownstreamMcpError as exc:
        app.state.inflight_downstream_calls.pop(str(request_id), None)
        if service_id and exc.code != -32003:
            app.state.circuit_breaker.record_failure(service_id)
        if exc.code == -32003:
            await app.state.repository.log_audit(
                action="ssrf.block",
                resource_type="mcp_service",
                resource_id=route.get("service_id"),
                metadata={"url": route["endpoint_url"], "reason": str(exc)},
                request_id=request_log_id,
                ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/call",
            tool_name=exposed_name,
            status="error",
            error_code=exc.code,
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_log_id,
            service_id=route.get("service_id"),
            service_tool_id=route.get("service_tool_id"),
            downstream_tool_name=route.get("original_name"),
            error_message=str(exc),
            protocol_version=protocol_version,
            client_ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return jsonrpc_error(request_id, exc.code, str(exc))
    except UrlSafetyError as exc:
        app.state.inflight_downstream_calls.pop(str(request_id), None)
        await app.state.repository.log_audit(
            action="ssrf.block",
            resource_type="mcp_service",
            resource_id=route.get("service_id"),
            metadata={"url": route["endpoint_url"], "reason": str(exc)},
            request_id=request_log_id,
            ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        await app.state.repository.log_invocation(
            session_id=session_id,
            method="tools/call",
            tool_name=exposed_name,
            status="policy_denied",
            error_code="ssrf_block",
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_log_id,
            service_id=route.get("service_id"),
            service_tool_id=route.get("service_tool_id"),
            downstream_tool_name=route.get("original_name"),
            error_message=str(exc),
            protocol_version=protocol_version,
            client_ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return jsonrpc_result(request_id, tool_error_result("ssrf_block", "Downstream endpoint is blocked by CoreMCP policy"))
    finally:
        if service_id and stdio_client_for_call is not None:
            await _persist_stdio_state(app, service_id, stdio_client_for_call)


async def dispatch_mcp(app: FastAPI, payload: dict[str, Any], request: Request) -> tuple[dict[str, Any] | None, str | None]:
    request_id = _get_request_id(payload)
    if payload.get("jsonrpc") != JSONRPC_VERSION or not isinstance(payload.get("method"), str):
        return jsonrpc_error(request_id, -32600, "Invalid Request"), None

    method = payload["method"]
    if method == "initialize":
        return await _handle_initialize(app, payload, request)
    if method == "notifications/initialized":
        app.state.sessions.mark_initialized(request.headers.get("Mcp-Session-Id"))
        return None, None
    if method == "notifications/cancelled":
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        await _forward_downstream_cancellation(app, request, params=params, request_id=request_id)
        await app.state.repository.log_invocation(
            session_id=request.headers.get("Mcp-Session-Id"),
            method="notifications/cancelled",
            tool_name=None,
            status="cancelled",
            request_id=str(params.get("requestId") or request_id or "cancelled"),
            error_message=params.get("reason") if isinstance(params.get("reason"), str) else None,
            client_ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return None, None
    if method == "ping":
        return jsonrpc_result(request_id, {}), None
    if method == "tools/list":
        if not _mcp_has_scope(request, "mcp:tools.read"):
            return await _scope_denied_response(app, payload, request, required_scope="mcp:tools.read"), None
        return await _handle_tools_list(app, payload, request), None
    if method == "tools/call":
        if not _mcp_has_scope(request, "mcp:tools.call"):
            return await _scope_denied_response(app, payload, request, required_scope="mcp:tools.call"), None
        return await _handle_tools_call(app, payload, request), None
    if method == "resources/list":
        if not _mcp_has_scope(request, "mcp:tools.read"):
            return await _scope_denied_response(app, payload, request, required_scope="mcp:tools.read"), None
        return await _handle_resources_list(app, payload, request), None
    if method == "resources/templates/list":
        if not _mcp_has_scope(request, "mcp:tools.read"):
            return await _scope_denied_response(app, payload, request, required_scope="mcp:tools.read"), None
        return await _handle_resources_list(app, payload, request, method="resources/templates/list", result_key="resourceTemplates"), None
    if method == "resources/read":
        if not _mcp_has_scope(request, "mcp:tools.read"):
            return await _scope_denied_response(app, payload, request, required_scope="mcp:tools.read"), None
        return await _handle_resources_read(app, payload, request), None
    if method == "prompts/list":
        if not _mcp_has_scope(request, "mcp:tools.read"):
            return await _scope_denied_response(app, payload, request, required_scope="mcp:tools.read"), None
        return await _handle_prompts_list(app, payload, request), None
    if method == "prompts/get":
        if not _mcp_has_scope(request, "mcp:tools.read"):
            return await _scope_denied_response(app, payload, request, required_scope="mcp:tools.read"), None
        return await _handle_prompts_get(app, payload, request), None
    return jsonrpc_error(request_id, -32601, "Method not found"), None


async def dispatch_mcp_batch(
    app: FastAPI,
    payloads: list[Any],
    request: Request,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Dispatch a JSON-RPC 2.0 batch request sequentially.

    JSON-RPC notifications do not produce response items. Sequential dispatch
    keeps side-effect ordering deterministic for initialize/cancel/tools calls.
    """

    responses: list[dict[str, Any]] = []
    new_session_id: str | None = None
    for item in payloads:
        if not isinstance(item, dict):
            responses.append(jsonrpc_error(None, -32600, "Invalid Request"))
            continue
        response_payload, item_session_id = await dispatch_mcp(app, item, request)
        if item_session_id and new_session_id is None:
            new_session_id = item_session_id
        if response_payload is not None:
            responses.append(response_payload)
    return (responses or None), new_session_id


async def validate_service(
    app: FastAPI,
    service_id: str,
    *,
    job_id: str | None = None,
    correlation_id_value: str | None = None,
) -> dict[str, Any]:
    repository: Repository = app.state.repository
    service = await repository.get_mcp_service(service_id)
    if service is None:
        raise ValueError("service not found")

    stages: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    protocol_version = "2025-11-25"
    stdio_client: StdioMcpClient | None = None
    try:
        await repository.update_mcp_service(service_id, {"status": "validating"})
        if job_id:
            await repository.update_job(job_id, status="running", progress=0.2)

        transport_type = _transport_type(service)
        downstream_headers: dict[str, str] = {}
        checker: UrlSafetyChecker | None = None
        safety_result = None
        if transport_type == "stdio":
            _stdio_signature(service, app.state.settings)
            stdio_client = await _stdio_client_for_config(app, service)
            stages.append({"name": "stdio_config_check", "status": "success"})
        else:
            checker = UrlSafetyChecker(app.state.settings)
            safety_result = checker.assert_safe(service["endpoint_url"])
            downstream_headers = await _downstream_headers_for_service(app, service_id)
            stages.append({"name": "url_safety_check", "status": "success"})

        if stdio_client is not None:
            init_response = await stdio_client.request(
                method="initialize",
                params={"protocolVersion": protocol_version, "capabilities": {}, "clientInfo": {"name": "coremcp-validator", "version": app.state.settings.app_version}},
                request_id=f"validate-{service_id}-init",
                protocol_version=protocol_version,
                correlation_id=correlation_id_value,
            )
        else:
            init_response = await app.state.downstream.request(
                method="initialize",
                params={"protocolVersion": protocol_version, "capabilities": {}, "clientInfo": {"name": "coremcp-validator", "version": app.state.settings.app_version}},
                request_id=f"validate-{service_id}-init",
                protocol_version=protocol_version,
                url=service["endpoint_url"],
                downstream_headers=downstream_headers,
                url_safety_checker=checker,
                safety_result=safety_result,
                correlation_id=correlation_id_value,
                session_id_callback=_downstream_session_callback(app, service_id),
                notification_callback=_downstream_notification_callback(app, service_id=service_id, source="http"),
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
            await repository.update_job(job_id, status="running", progress=0.5)

        if stdio_client is not None:
            tools_response = await stdio_client.request(
                method="tools/list",
                params={},
                request_id=f"validate-{service_id}-tools",
                protocol_version=protocol_version,
                correlation_id=correlation_id_value,
            )
        else:
            tools_response = await app.state.downstream.request(
                method="tools/list",
                params={},
                request_id=f"validate-{service_id}-tools",
                protocol_version=protocol_version,
                session_id=_downstream_session_id(app, service_id),
                url=service["endpoint_url"],
                downstream_headers=downstream_headers,
                url_safety_checker=checker,
                safety_result=safety_result,
                correlation_id=correlation_id_value,
                session_id_callback=_downstream_session_callback(app, service_id),
                notification_callback=_downstream_notification_callback(app, service_id=service_id, source="http"),
            )
        result = tools_response.get("result")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise DownstreamMcpError("downstream tools/list returned invalid tools")
        stages.append({"name": "tools_list", "status": "success", "tools_found": len(tools)})

        existing_tools = await repository.list_service_tools(service_id)
        normalized, metadata_warnings = normalize_downstream_tools(tools, service_slug=service["slug"], settings=app.state.settings)
        warnings.extend(metadata_warnings)
        if tools and not normalized:
            raise DownstreamMcpError("downstream tools/list returned no valid tools", code=-32602)
        schema_diff = _tool_schema_diff(existing_tools, normalized)
        change_summary = schema_diff["summary"]
        saved = await repository.replace_service_tools(service_id, normalized)
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
        await repository.mark_service_validated(
            service_id=service_id,
            status="active",
            protocol_version=protocol_version,
            summary=summary,
            capabilities=downstream_capabilities,
        )
        await repository.log_audit(
            action="service.validate.success",
            resource_type="mcp_service",
            resource_id=service_id,
            metadata=summary,
            request_id=correlation_id_value,
        )
        if job_id:
            await repository.update_job(job_id, status="success", progress=1.0, result=summary)
        await _publish_list_changed(app, reason="service.validate.success", resource_id=service_id)
        return {"service_id": service_id, "status": "success", **summary}
    except UrlSafetyError as exc:
        summary = {"stages": stages + [{"name": "url_safety_check", "status": "failed"}], "tools_found": 0, "warnings": warnings, "error": str(exc)}
        await repository.mark_service_validated(service_id=service_id, status="error", protocol_version=None, summary=summary)
        await repository.log_audit(
            action="ssrf.block",
            resource_type="mcp_service",
            resource_id=service_id,
            metadata={"url": service["endpoint_url"], "reason": str(exc)},
            request_id=correlation_id_value,
        )
        if job_id:
            await repository.update_job(job_id, status="failed", progress=1.0, error=summary)
        await _publish_list_changed(app, reason="service.validate.failed", resource_id=service_id)
        raise
    except DownstreamMcpError as exc:
        summary = {"stages": stages, "tools_found": 0, "warnings": warnings, "error": str(exc)}
        existing_tools = await repository.list_service_tools(service_id)
        preserve_active_catalog = bool(existing_tools and service["status"] == "active")
        await repository.mark_service_validated(
            service_id=service_id,
            status="active" if preserve_active_catalog else "error",
            protocol_version=protocol_version,
            summary={**summary, "preserved_active_catalog": preserve_active_catalog},
        )
        await repository.log_audit(
            action="ssrf.block" if exc.code == -32003 else "service.validate.failed",
            resource_type="mcp_service",
            resource_id=service_id,
            metadata={**summary, "preserved_active_catalog": preserve_active_catalog},
            request_id=correlation_id_value,
        )
        if job_id:
            await repository.update_job(job_id, status="failed", progress=1.0, error={**summary, "preserved_active_catalog": preserve_active_catalog})
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
        app.state.auth_rate_limiter = FixedWindowRateLimiter()
        app.state.mcp_rate_limiter = FixedWindowRateLimiter()
        app.state.service_rate_limiter = FixedWindowRateLimiter()
        app.state.oauth_dcr_rate_limiter = FixedWindowRateLimiter()
        app.state.oauth_cimd_rate_limiter = FixedWindowRateLimiter()
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

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID")
        request_id = incoming.strip() if incoming and incoming.strip() else f"req_{secrets.token_hex(16)}"
        request.state.request_id = request_id
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                response = api_error("invalid_content_length", "Content-Length must be an integer", status_code=400)
                response.headers["X-Request-ID"] = request_id
                return response
            if length > settings.max_request_body_bytes:
                response = api_error(
                    "request_too_large",
                    f"request body exceeds {settings.max_request_body_bytes} bytes",
                    status_code=413,
                )
                response.headers["X-Request-ID"] = request_id
                return response
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
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/live")
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/ready")
    async def ready(request: Request) -> dict[str, str]:
        db_ok = await request.app.state.repository.healthcheck()
        vault_ok = await request.app.state.vault.is_ready()
        return {"status": "ready" if db_ok and vault_ok else "degraded"}

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        if not request.app.state.settings.metrics_enabled:
            return Response(status_code=404)
        snapshot = await request.app.state.repository.metrics_snapshot()
        snapshot["active_mcp_sessions"] = request.app.state.sessions.count_active()
        return Response(
            _prometheus_metrics(snapshot),
            media_type="text/plain; version=0.0.4; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/.well-known/oauth-protected-resource")
    async def protected_resource_metadata(request: Request) -> Response:
        settings_obj: Settings = request.app.state.settings
        if settings_obj.auth_mode != "oauth" and not settings_obj.expose_resource_metadata_in_static_mode:
            return Response(status_code=404)
        resource = str(request.url_for("mcp"))
        payload: dict[str, Any] = {
            "resource": resource,
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp:tools.read", "mcp:tools.call", "mcp:connections.manage"],
        }
        if settings_obj.auth_mode == "oauth":
            payload["authorization_servers"] = [str(request.base_url).rstrip("/")]
        return JSONResponse(payload)

    @app.get("/.well-known/oauth-authorization-server")
    async def authorization_server_metadata(request: Request) -> Response:
        if request.app.state.settings.auth_mode != "oauth":
            return Response(status_code=404)
        issuer = str(request.base_url).rstrip("/")
        return JSONResponse(
            {
                "issuer": issuer,
                "authorization_endpoint": f"{issuer}/oauth/authorize",
                "token_endpoint": f"{issuer}/oauth/token",
                "registration_endpoint": f"{issuer}/oauth/register",
                "revocation_endpoint": f"{issuer}/oauth/revoke",
                "introspection_endpoint": f"{issuer}/oauth/introspect",
                "jwks_uri": f"{issuer}/.well-known/jwks.json",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "registration_endpoint_auth_methods_supported": ["none"],
                "client_id_metadata_document_supported": True,
                "client_id_metadata_document_required": False,
            }
        )

    @app.get("/.well-known/jwks.json")
    async def jwks(request: Request) -> Response:
        if request.app.state.settings.auth_mode != "oauth":
            return Response(status_code=404)
        return JSONResponse(request.app.state.oauth.jwks(), headers={"Cache-Control": "no-store"})

    @app.post("/oauth/register")
    async def oauth_register(request: Request) -> Response:
        if request.app.state.settings.auth_mode != "oauth":
            return Response(status_code=404)
        try:
            if limited := _check_oauth_dcr_rate_limit(request):
                return _oauth_error_response(limited)
            body = await _form_or_json_body(request)
            client = await request.app.state.oauth.register_client(body)
            return JSONResponse(
                {
                    "client_id": client.client_id,
                    "client_id_issued_at": int(time.time()),
                    "client_name": client.client_name,
                    "redirect_uris": client.redirect_uris,
                    "grant_types": client.grant_types or ["authorization_code", "refresh_token"],
                    "response_types": client.response_types or ["code"],
                    "token_endpoint_auth_method": "none",
                    "scope": client.scope,
                },
                status_code=201,
                headers={"Cache-Control": "no-store"},
            )
        except OAuthError as exc:
            return _oauth_error_response(exc)

    @app.get("/oauth/authorize")
    async def oauth_authorize(request: Request) -> Response:
        if request.app.state.settings.auth_mode != "oauth":
            return Response(status_code=404)
        query = request.query_params
        if query.get("response_type") != "code":
            return _oauth_error_response(OAuthError("unsupported_response_type", "only response_type=code is supported"))
        resource = query.get("resource") or oauth_resource(request)
        if resource != oauth_resource(request):
            return _oauth_error_response(OAuthError("invalid_target", "resource must match CoreMCP /mcp"))
        try:
            code = await request.app.state.oauth.create_authorization_code(
                client_id=str(query.get("client_id") or ""),
                redirect_uri=str(query.get("redirect_uri") or ""),
                resource=resource,
                scope=str(query.get("scope") or "mcp:tools.read mcp:tools.call"),
                code_challenge=str(query.get("code_challenge") or ""),
                code_challenge_method=str(query.get("code_challenge_method") or ""),
                client_ip=request_ip(request),
            )
            location = request.app.state.oauth.redirect_with_code(
                str(query.get("redirect_uri")),
                code=code,
                state=query.get("state"),
            )
            return RedirectResponse(location, status_code=302)
        except OAuthError as exc:
            return _oauth_error_response(exc)

    @app.post("/oauth/token")
    async def oauth_token(request: Request) -> Response:
        if request.app.state.settings.auth_mode != "oauth":
            return Response(status_code=404)
        try:
            body = await _form_or_json_body(request)
            grant_type = body.get("grant_type")
            resource = str(body.get("resource") or oauth_resource(request))
            if resource != oauth_resource(request):
                raise OAuthError("invalid_target", "resource must match CoreMCP /mcp")
            if grant_type == "authorization_code":
                payload = await request.app.state.oauth.exchange_authorization_code(
                    code=str(body.get("code") or ""),
                    client_id=str(body.get("client_id") or ""),
                    redirect_uri=str(body.get("redirect_uri") or ""),
                    code_verifier=str(body.get("code_verifier") or ""),
                    resource=resource,
                    issuer=oauth_issuer(request),
                    client_ip=request_ip(request),
                )
            elif grant_type == "refresh_token":
                payload = await request.app.state.oauth.refresh(
                    refresh_token=str(body.get("refresh_token") or ""),
                    client_id=str(body.get("client_id") or ""),
                    resource=resource,
                    issuer=oauth_issuer(request),
                    client_ip=request_ip(request),
                )
            else:
                raise OAuthError("unsupported_grant_type", "grant_type must be authorization_code or refresh_token")
            return JSONResponse(payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
        except OAuthError as exc:
            return _oauth_error_response(exc)

    @app.post("/oauth/revoke")
    async def oauth_revoke(request: Request) -> Response:
        if request.app.state.settings.auth_mode != "oauth":
            return Response(status_code=404)
        try:
            body = await _form_or_json_body(request)
            await request.app.state.oauth.revoke(str(body.get("token") or ""))
            return JSONResponse({"revoked": True}, headers={"Cache-Control": "no-store"})
        except OAuthError as exc:
            return _oauth_error_response(exc)

    @app.post("/oauth/introspect")
    async def oauth_introspect(request: Request) -> Response:
        if request.app.state.settings.auth_mode != "oauth":
            return Response(status_code=404)
        try:
            body = await _form_or_json_body(request)
            payload = await request.app.state.oauth.introspect(
                str(body.get("token") or ""),
                issuer=oauth_issuer(request),
                audience=oauth_resource(request),
            )
            return JSONResponse(payload, headers={"Cache-Control": "no-store"})
        except OAuthError as exc:
            return _oauth_error_response(exc)

    @app.post("/mcp")
    async def mcp(request: Request) -> Response:
        if not await verify_mcp_request(request):
            await request.app.state.repository.log_audit(
                action="auth.failure",
                resource_type="mcp",
                request_id=correlation_id(request),
                ip=request_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            return unauthorized_response(request)

        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse(jsonrpc_error(None, -32700, "Parse error"), status_code=400)
        if isinstance(payload, list) and not payload:
            return JSONResponse(jsonrpc_error(None, -32600, "Invalid Request"), status_code=400)
        if not isinstance(payload, (dict, list)):
            return JSONResponse(jsonrpc_error(None, -32600, "Invalid Request"), status_code=400)

        request.app.state.sessions.touch(request.headers.get("Mcp-Session-Id"))
        request.app.state.sessions.reap_idle(SESSION_IDLE_REAP_SECONDS)
        if isinstance(payload, list):
            response_payload, new_session_id = await dispatch_mcp_batch(request.app, payload, request)
        else:
            response_payload, new_session_id = await dispatch_mcp(request.app, payload, request)
        if response_payload is None:
            return Response(status_code=202)

        headers = {}
        if new_session_id:
            headers["Mcp-Session-Id"] = new_session_id
        elif request.headers.get("Mcp-Session-Id"):
            headers["Mcp-Session-Id"] = request.headers["Mcp-Session-Id"]
        return JSONResponse(response_payload, headers=headers)

    @app.get("/mcp")
    async def mcp_sse(request: Request, max_events: int | None = None, heartbeat_seconds: float = 15.0) -> Response:
        if not await verify_mcp_request(request):
            return unauthorized_response(request)
        last_event_id = _parse_last_event_id(request.headers.get("Last-Event-Id"))

        async def events():
            subscription = await request.app.state.list_changed_bus.subscribe(last_event_id=last_event_id)
            try:
                yield ": CoreMCP SSE keepalive\n\n"
                if max_events == 0:
                    return
                emitted = 0
                heartbeat = max(0.1, min(heartbeat_seconds, 60.0))
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(subscription.get(), timeout=heartbeat)
                    except TimeoutError:
                        yield ": CoreMCP SSE keepalive\n\n"
                        continue
                    emitted += 1
                    yield (
                        f"id: {event.id}\n"
                        f"event: {event.event}\n"
                        f"data: {json.dumps(event.data, ensure_ascii=False, separators=(',', ':'))}\n\n"
                    )
                    if max_events is not None and emitted >= max_events:
                        return
            finally:
                await subscription.close()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.delete("/mcp")
    async def mcp_delete(request: Request) -> Response:
        if not await verify_mcp_request(request):
            return unauthorized_response(request)
        request.app.state.sessions.delete(request.headers.get("Mcp-Session-Id"))
        return Response(status_code=204)

    @app.get("/v1/me")
    async def me(request: Request) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        return JSONResponse(await request.app.state.repository.get_me())

    @app.get("/v1/settings")
    async def settings_endpoint(request: Request) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        settings_obj: Settings = request.app.state.settings
        token_count = await request.app.state.repository.count_active_client_tokens()
        return JSONResponse(
            {
                "admin_token_masked": "cmcp_admin_••••",
                "client_token_count": token_count,
                "auth_mode": settings_obj.auth_mode,
                "oauth_enabled": settings_obj.auth_mode == "oauth",
                "secret_backend": settings_obj.secret_backend,
                "tailscale_enabled": False,
                "cache_backend": "memory",
                "app_version": settings_obj.app_version,
            }
        )

    @app.get("/v1/dashboard/summary")
    async def dashboard_summary(request: Request) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        return JSONResponse(await request.app.state.repository.dashboard_summary())

    @app.post("/v1/settings/admin-token/rotate")
    async def rotate_admin_token(request: Request) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        settings_obj: Settings = request.app.state.settings
        new_token = generate_admin_token()
        try:
            write_admin_token_atomic(settings_obj.resolved_admin_token_file, new_token)
        except AdminTokenFileError as exc:
            return api_error(
                "admin_token_file_unavailable",
                (
                    "Admin token rotation requires a writable COREMCP_ADMIN_TOKEN_FILE; "
                    "env-only COREMCP_ADMIN_TOKEN_VALUE cannot be rotated at runtime."
                ),
                status_code=409,
                details={"reason": str(exc)},
            )
        await request.app.state.repository.log_audit(
            action="admin_token.rotate",
            resource_type="admin_token",
            metadata={"admin_token_masked": mask_secret(new_token), "expires_at": None},
            request_id=correlation_id(request),
            ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return JSONResponse(
            {"new_token": new_token, "admin_token_masked": mask_secret(new_token), "expires_at": None},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/v1/settings/client-tokens")
    async def list_client_tokens(request: Request, limit: int = 50) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        items = await request.app.state.repository.list_personal_access_tokens(limit=max(1, min(limit, 100)))
        return JSONResponse({"items": items, "next_cursor": None})

    @app.post("/v1/settings/client-tokens")
    async def issue_client_token(request: Request) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        external_connection_id = body.get("external_connection_id")
        if not isinstance(external_connection_id, str):
            return api_error("validation_failed", "external_connection_id is required", status_code=422)
        scopes = _validated_scopes(body.get("scopes"))
        if scopes is None:
            return api_error("validation_failed", "scopes must be supported MCP scopes", status_code=422)
        try:
            item = await ClientTokenService(request.app.state.repository).issue(
                external_connection_id=external_connection_id,
                scopes=scopes,
                protocol_version=body.get("protocol_version") if isinstance(body.get("protocol_version"), str) else None,
            )
        except ValueError as exc:
            return api_error("validation_failed", str(exc), status_code=422)
        return JSONResponse(item, status_code=201)

    @app.delete("/v1/settings/client-tokens/{token_id}")
    async def revoke_client_token(request: Request, token_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        await ClientTokenService(request.app.state.repository).revoke(token_id)
        return accepted({"id": token_id, "status": "revoked"})

    @app.post("/v1/external-connections/one-time-token")
    async def create_one_time_connection_token(request: Request) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        scopes = _validated_scopes(body.get("requested_scopes", body.get("scopes")))
        if scopes is None:
            return api_error("validation_failed", "requested_scopes must be supported MCP scopes", status_code=422)
        client_type = str(body.get("client_type") or "openclaw")
        toolbox_id = body.get("toolbox_id") if isinstance(body.get("toolbox_id"), str) else DEFAULT_TOOLBOX_ID
        token = _generate_one_time_token()
        expires_at = _utc_sql_timestamp(time.time() + ONE_TIME_TOKEN_TTL_SECONDS)
        try:
            record = await request.app.state.repository.create_connection_token(
                token_hash=hash_token(token),
                client_type=client_type,
                toolbox_id=toolbox_id,
                requested_scopes=scopes,
                expires_at=expires_at,
                created_ip=request_ip(request),
                created_user_agent=request.headers.get("user-agent"),
            )
        except sqlite3.IntegrityError as exc:
            return api_error("validation_failed", str(exc), status_code=422)
        expires_at_iso = _iso_z(record["expires_at"])
        return JSONResponse(
            {
                "token": token,
                "token_type": "coremcp_one_time",
                "expires_in": ONE_TIME_TOKEN_TTL_SECONDS,
                "expires_at": expires_at_iso,
                "client_type": record["client_type"],
                "toolbox_id": record["toolbox_id"],
                "requested_scopes": record["requested_scopes"],
                "connection_prompt": _connection_token_prompt(token, expires_at_iso),
            },
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/v1/external-connections/exchange")
    async def exchange_one_time_connection_token(request: Request) -> Response:
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        presented = body.get("one_time_token") or body.get("token")
        if not isinstance(presented, str) or not presented.startswith(ONE_TIME_TOKEN_PREFIX):
            return api_error("invalid_token", "one-time token is invalid or expired", status_code=401)
        token_record = await request.app.state.repository.consume_connection_token(
            token_hash=hash_token(presented),
            used_ip=request_ip(request),
            used_user_agent=request.headers.get("user-agent"),
        )
        if token_record is None:
            return api_error("invalid_token", "one-time token is invalid or expired", status_code=401)
        requested_client_type = body.get("client_type")
        if isinstance(requested_client_type, str) and requested_client_type != token_record["client_type"]:
            return api_error("validation_failed", "client_type does not match one-time token", status_code=422)
        client_name = body.get("client_name") if isinstance(body.get("client_name"), str) else token_record["client_type"]
        protocol_version = body.get("protocol_version") if isinstance(body.get("protocol_version"), str) else None
        try:
            connection = await request.app.state.repository.create_external_connection(
                client_type=token_record["client_type"],
                client_name=client_name,
                toolbox_id=token_record["toolbox_id"],
                protocol_version=protocol_version,
                scopes=token_record["requested_scopes"],
                created_ip=request_ip(request),
                created_user_agent=request.headers.get("user-agent"),
            )
            issued = await ClientTokenService(request.app.state.repository).issue(
                external_connection_id=connection["id"],
                scopes=token_record["requested_scopes"],
                protocol_version=protocol_version,
            )
        except (ValueError, sqlite3.IntegrityError) as exc:
            return api_error("validation_failed", str(exc), status_code=422)
        return JSONResponse(
            {
                "access_token": issued["token"],
                "token_type": "Bearer",
                "expires_in": None,
                "connection_id": connection["id"],
                "token_id": issued["id"],
                "token_prefix": issued["token_prefix"],
                "scopes": issued["scopes"],
                "protocol_version": issued["protocol_version"],
            },
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/v1/external-connections")
    async def list_external_connections(request: Request, limit: int = 50) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        items = await request.app.state.repository.list_external_connections(limit=max(1, min(limit, 100)))
        return JSONResponse({"items": items, "next_cursor": None})

    @app.post("/v1/external-connections")
    async def create_external_connection(request: Request) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        client_type = str(body.get("client_type") or "other")
        client_name = body.get("client_name") if isinstance(body.get("client_name"), str) else client_type
        try:
            item = await request.app.state.repository.create_external_connection(
                client_type=client_type,
                client_name=client_name,
                toolbox_id=body.get("toolbox_id") if isinstance(body.get("toolbox_id"), str) else DEFAULT_TOOLBOX_ID,
                protocol_version=body.get("protocol_version") if isinstance(body.get("protocol_version"), str) else None,
                scopes=body.get("scopes") if isinstance(body.get("scopes"), list) else None,
                created_ip=request_ip(request),
                created_user_agent=request.headers.get("user-agent"),
            )
        except sqlite3.IntegrityError as exc:
            return api_error("validation_failed", str(exc), status_code=422)
        return JSONResponse(item, status_code=201)

    @app.delete("/v1/external-connections/{connection_id}")
    async def revoke_external_connection(request: Request, connection_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        await request.app.state.repository.revoke_external_connection(connection_id)
        return accepted({"id": connection_id, "status": "revoked"})

    @app.get("/v1/mcp-services")
    async def list_services(request: Request, limit: int = 50, status: str | None = None) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        items = await request.app.state.repository.list_mcp_services(limit=max(1, min(limit, 100)), status=status)
        return JSONResponse({"items": items, "next_cursor": None})

    @app.post("/v1/mcp-services")
    async def create_service(request: Request) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        name = body.get("name")
        transport_type = body.get("transport_type") if isinstance(body.get("transport_type"), str) else "http"
        if transport_type not in SERVICE_TRANSPORT_TYPES:
            return api_error("validation_failed", "transport_type must be http or stdio", status_code=422)
        endpoint_url = body.get("endpoint_url")
        if not isinstance(name, str) or not name.strip():
            return api_error("validation_failed", "name is required", status_code=422)
        slug = body.get("slug") if isinstance(body.get("slug"), str) and body.get("slug") else slugify_tool_name(name).lower()
        stdio_command = body.get("stdio_command") if isinstance(body.get("stdio_command"), str) else None
        if transport_type == "http":
            if not isinstance(endpoint_url, str) or not endpoint_url.strip():
                return api_error("validation_failed", "endpoint_url is required for http transport", status_code=422)
            endpoint_value = endpoint_url.strip()
        else:
            if not stdio_command or not stdio_command.strip():
                return api_error("validation_failed", "stdio_command is required for stdio transport", status_code=422)
            runtime_error = _validate_stdio_runtime_config(
                stdio_command,
                body.get("stdio_cwd") if isinstance(body.get("stdio_cwd"), str) else None,
            )
            if runtime_error:
                return api_error("validation_failed", runtime_error, status_code=422)
            endpoint_value = endpoint_url.strip() if isinstance(endpoint_url, str) and endpoint_url.strip() else f"stdio://{slug}"
        try:
            service = await request.app.state.repository.create_mcp_service(
                name=name.strip(),
                slug=slug,
                endpoint_url=endpoint_value,
                auth_type=body.get("auth_type") if isinstance(body.get("auth_type"), str) else "none",
                description=body.get("description") if isinstance(body.get("description"), str) else None,
                category=body.get("category") if isinstance(body.get("category"), str) else None,
                logo_url=body.get("logo_url") if isinstance(body.get("logo_url"), str) else None,
                homepage_url=body.get("homepage_url") if isinstance(body.get("homepage_url"), str) else None,
                documentation_url=body.get("documentation_url") if isinstance(body.get("documentation_url"), str) else None,
                transport_type=transport_type,
                stdio_command=stdio_command.strip() if stdio_command else None,
                stdio_args=_string_list(body.get("stdio_args")),
                stdio_env=_stdio_env(body.get("stdio_env")),
                stdio_cwd=body.get("stdio_cwd") if isinstance(body.get("stdio_cwd"), str) else None,
                stdio_idle_timeout_seconds=_positive_int(
                    body.get("stdio_idle_timeout_seconds"),
                    _stdio_default_idle_timeout(request.app.state.settings),
                ),
            )
        except sqlite3.IntegrityError as exc:
            return api_error("conflict", "service slug already exists", status_code=409, details=str(exc))
        credential = body.get("credential")
        if isinstance(credential, dict) and isinstance(credential.get("value"), str):
            secret_ref = await request.app.state.vault.put(service_id=service["id"], secret=credential["value"])
            await request.app.state.repository.upsert_service_credential(
                service_id=service["id"],
                credential_type=str(credential.get("type") or service.get("auth_type") or "bearer_token"),
                secret_ref=secret_ref,
                masked_value=mask_secret(credential["value"]),
                header_name=credential.get("header_name") if isinstance(credential.get("header_name"), str) else None,
            )
        return JSONResponse(service, status_code=201)

    @app.get("/v1/mcp-services/{service_id}")
    async def get_service(request: Request, service_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        service = await request.app.state.repository.get_mcp_service(service_id)
        if service is None:
            return not_found("mcp_service")
        return JSONResponse(service)

    @app.patch("/v1/mcp-services/{service_id}")
    async def patch_service(request: Request, service_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        updates = {
            key: body[key]
            for key in (
                "name",
                "slug",
                "description",
                "endpoint_url",
                "auth_type",
                "status",
                "category",
                "logo_url",
                "homepage_url",
                "documentation_url",
                "transport_type",
                "stdio_command",
                "stdio_args",
                "stdio_env",
                "stdio_cwd",
                "stdio_idle_timeout_seconds",
            )
            if key in body
        }
        if updates.get("transport_type") not in {None, "http", "stdio"}:
            return api_error("validation_failed", "transport_type must be http or stdio", status_code=422)
        if "stdio_args" in updates:
            updates["stdio_args"] = _string_list(updates["stdio_args"])
        if "stdio_env" in updates:
            updates["stdio_env"] = _stdio_env(updates["stdio_env"])
        if "stdio_idle_timeout_seconds" in updates:
            updates["stdio_idle_timeout_seconds"] = _positive_int(
                updates["stdio_idle_timeout_seconds"],
                _stdio_default_idle_timeout(request.app.state.settings),
            )
        if updates.get("transport_type") == "stdio" or "stdio_command" in updates or "stdio_cwd" in updates:
            current = await request.app.state.repository.get_mcp_service(service_id)
            command = updates.get("stdio_command", current.get("stdio_command") if current else None)
            cwd = updates.get("stdio_cwd", current.get("stdio_cwd") if current else None)
            if updates.get("transport_type", current.get("transport_type") if current else None) == "stdio":
                runtime_error = _validate_stdio_runtime_config(command if isinstance(command, str) else None, cwd if isinstance(cwd, str) else None)
                if runtime_error:
                    return api_error("validation_failed", runtime_error, status_code=422)
        service = await request.app.state.repository.update_mcp_service(service_id, updates)
        if service is None:
            return not_found("mcp_service")
        if (
            ("status" in updates and str(updates.get("status") or "") != "active")
            or ("transport_type" in updates and updates.get("transport_type") != "stdio")
        ):
            await _close_stdio_client_for_service(request.app, service_id)
        if (
            "endpoint_url" in updates
            or "transport_type" in updates
            or ("status" in updates and str(updates.get("status") or "") != "active")
        ):
            _forget_downstream_session(request.app, service_id)
        await _publish_list_changed(request.app, reason="service.update", resource_id=service_id)
        return JSONResponse(service)

    @app.delete("/v1/mcp-services/{service_id}")
    async def delete_service(request: Request, service_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        await request.app.state.repository.soft_delete_mcp_service(service_id)
        await _close_stdio_client_for_service(request.app, service_id)
        _forget_downstream_session(request.app, service_id)
        await _publish_list_changed(request.app, reason="service.delete", resource_id=service_id)
        return accepted({"id": service_id, "status": "deleted"})

    @app.post("/v1/mcp-services/{service_id}/validate")
    async def validate_service_endpoint(request: Request, service_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        if await request.app.state.repository.get_mcp_service(service_id) is None:
            return not_found("mcp_service")
        job = await request.app.state.repository.create_job(kind="service_validation", payload={"service_id": service_id})
        try:
            report = await validate_service(request.app, service_id, job_id=job["id"], correlation_id_value=correlation_id(request))
            return JSONResponse({"job_id": job["id"], **report})
        except UrlSafetyError as exc:
            return api_error("unsafe_endpoint", str(exc), status_code=400, details={"job_id": job["id"]})
        except DownstreamMcpError as exc:
            return api_error("validation_failed", str(exc), status_code=400, details={"job_id": job["id"]})

    @app.get("/v1/mcp-services/{service_id}/tools")
    async def service_tools(request: Request, service_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        if await request.app.state.repository.get_mcp_service(service_id) is None:
            return not_found("mcp_service")
        items = await request.app.state.repository.list_service_tools(service_id)
        return JSONResponse({"items": items, "next_cursor": None})

    @app.get("/v1/mcp-services/{service_id}/tool-overrides")
    async def service_tool_overrides(request: Request, service_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        if await request.app.state.repository.get_mcp_service(service_id) is None:
            return not_found("mcp_service")
        items = await request.app.state.repository.list_tool_overrides(service_id)
        return JSONResponse({"items": items, "next_cursor": None})

    @app.put("/v1/mcp-services/{service_id}/tool-overrides/{service_tool_id}")
    async def put_service_tool_override(request: Request, service_id: str, service_tool_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        permission_level = str(body.get("permission_level") or "callable")
        if permission_level not in TOOL_PERMISSION_LEVELS:
            return api_error("validation_failed", "permission_level must be hidden, visible_only, or callable", status_code=422)
        item = await request.app.state.repository.upsert_tool_override(
            service_id=service_id,
            service_tool_id=service_tool_id,
            enabled=bool(body.get("enabled", True)),
            permission_level=permission_level,
        )
        if item is None:
            return not_found("service_tool")
        await _publish_list_changed(
            request.app,
            reason="tool_permission.update",
            resource_id=service_tool_id,
            categories=("tools",),
        )
        return JSONResponse(item)

    @app.post("/v1/mcp-services/{service_id}/tool-overrides/preset")
    async def apply_service_tool_preset(request: Request, service_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        preset = str(body.get("preset") or "")
        if preset not in TOOL_PRESETS:
            return api_error("validation_failed", "preset must be readonly, full_access, or dangerous_off", status_code=422)
        if await request.app.state.repository.get_mcp_service(service_id) is None:
            return not_found("mcp_service")

        tools = await request.app.state.repository.list_service_tools(service_id)
        items: list[dict[str, Any]] = []
        for tool in tools:
            enabled, permission_level = _tool_preset_policy(tool, preset)
            item = await request.app.state.repository.upsert_tool_override(
                service_id=service_id,
                service_tool_id=tool["id"],
                enabled=enabled,
                permission_level=permission_level,
            )
            if item is not None:
                items.append(item)
        await request.app.state.repository.log_audit(
            action="tool_permission.preset",
            resource_type="mcp_service",
            resource_id=service_id,
            metadata={"preset": preset, "counts": _tool_override_counts(items)},
            request_id=correlation_id(request),
        )
        await _publish_list_changed(
            request.app,
            reason=f"tool_permission.preset.{preset}",
            resource_id=service_id,
            categories=("tools",),
        )
        return JSONResponse({"preset": preset, "items": items, "counts": _tool_override_counts(items), "next_cursor": None})

    @app.put("/v1/mcp-services/{service_id}/credential")
    async def put_credential(request: Request, service_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        if await request.app.state.repository.get_mcp_service(service_id) is None:
            return not_found("mcp_service")
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        secret = body.get("secret")
        credential_type = body.get("credential_type")
        if not isinstance(secret, str) or not secret:
            return api_error("validation_failed", "secret is required", status_code=422)
        if not isinstance(credential_type, str):
            credential_type = "bearer_token"
        secret_ref = await request.app.state.vault.put(service_id=service_id, secret=secret)
        item = await request.app.state.repository.upsert_service_credential(
            service_id=service_id,
            credential_type=credential_type,
            secret_ref=secret_ref,
            masked_value=mask_secret(secret),
            header_name=body.get("header_name") if isinstance(body.get("header_name"), str) else None,
        )
        return JSONResponse({"status": item["status"], "masked": item["masked_value"], "updated_at": item["updated_at"]})

    @app.post("/v1/mcp-services/{service_id}/credential/rotate")
    async def rotate_credential(request: Request, service_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        if await request.app.state.repository.get_mcp_service(service_id) is None:
            return not_found("mcp_service")
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        secret = body.get("secret")
        credential_type = body.get("credential_type")
        if not isinstance(secret, str) or not secret:
            return api_error("validation_failed", "secret is required", status_code=422)
        if not isinstance(credential_type, str):
            existing = await request.app.state.repository.get_service_credential(service_id)
            credential_type = existing["credential_type"] if existing else "bearer_token"
        previous = await request.app.state.repository.get_service_credential(service_id)
        secret_ref = await request.app.state.vault.put(service_id=service_id, secret=secret)
        item = await request.app.state.repository.upsert_service_credential(
            service_id=service_id,
            credential_type=credential_type,
            secret_ref=secret_ref,
            masked_value=mask_secret(secret),
            header_name=body.get("header_name") if isinstance(body.get("header_name"), str) else (previous or {}).get("header_name"),
        )
        if previous:
            await request.app.state.vault.delete(previous["secret_ref"])
        await request.app.state.repository.log_audit(
            action="credential.rotate",
            resource_type="mcp_service",
            resource_id=service_id,
            ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return JSONResponse({"status": item["status"], "masked": item["masked_value"], "updated_at": item["updated_at"]})

    @app.get("/v1/mcp-services/{service_id}/credential")
    async def get_credential(request: Request, service_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        item = await request.app.state.repository.get_service_credential(service_id)
        if item is None:
            return JSONResponse({"status": "not_connected", "masked": None, "updated_at": None})
        return JSONResponse({"status": item["status"], "masked": item["masked_value"], "updated_at": item["updated_at"]})

    @app.delete("/v1/mcp-services/{service_id}/credential")
    async def delete_credential(request: Request, service_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        item = await request.app.state.repository.get_service_credential(service_id)
        if item:
            await request.app.state.vault.delete(item["secret_ref"])
        await request.app.state.repository.revoke_service_credential(service_id)
        await request.app.state.repository.update_mcp_service(service_id, {"status": "auth_required"})
        await _close_stdio_client_for_service(request.app, service_id)
        await _publish_list_changed(request.app, reason="credential.delete", resource_id=service_id)
        return accepted({"service_id": service_id, "status": "not_connected"})

    @app.get("/v1/toolboxes")
    async def list_toolboxes(request: Request, limit: int = 50) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        items = await request.app.state.repository.list_toolboxes(limit=max(1, min(limit, 100)))
        return JSONResponse({"items": items, "next_cursor": None})

    @app.get("/v1/toolboxes/{toolbox_id}")
    async def get_toolbox(request: Request, toolbox_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        toolboxes = await request.app.state.repository.list_toolboxes(limit=100)
        toolbox = next((item for item in toolboxes if item["id"] == toolbox_id), None)
        if toolbox is None:
            return not_found("toolbox")
        items = await request.app.state.repository.list_toolbox_items(toolbox_id)
        return JSONResponse({**toolbox, "items": items})

    @app.post("/v1/toolboxes/{toolbox_id}/items")
    async def add_toolbox_item(request: Request, toolbox_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        service_id = body.get("service_id")
        if not isinstance(service_id, str):
            return api_error("validation_failed", "service_id is required", status_code=422)
        if await request.app.state.repository.get_mcp_service(service_id) is None:
            return not_found("mcp_service")
        item = await request.app.state.repository.add_toolbox_item(
            toolbox_id, service_id, enabled=bool(body.get("enabled", True))
        )
        await _publish_list_changed(
            request.app,
            reason="toolbox_item.upsert",
            resource_id=item.get("id"),
            categories=("tools",),
        )
        return JSONResponse(item, status_code=201)

    @app.patch("/v1/toolboxes/{toolbox_id}/items/{item_id}")
    async def patch_toolbox_item(request: Request, toolbox_id: str, item_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        item = await request.app.state.repository.update_toolbox_item(item_id, enabled=bool(body.get("enabled", True)))
        if item is None or item["toolbox_id"] != toolbox_id:
            return not_found("toolbox_item")
        await _publish_list_changed(
            request.app,
            reason="toolbox_item.update",
            resource_id=item_id,
            categories=("tools",),
        )
        return JSONResponse(item)

    @app.delete("/v1/toolboxes/{toolbox_id}/items/{item_id}")
    async def delete_toolbox_item(request: Request, toolbox_id: str, item_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        await request.app.state.repository.delete_toolbox_item(item_id)
        await _publish_list_changed(
            request.app,
            reason="toolbox_item.delete",
            resource_id=item_id,
            categories=("tools",),
        )
        return accepted({"id": item_id, "status": "deleted"})

    @app.get("/v1/playground/tools/list")
    async def playground_tools_list(request: Request) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        session_id = request.headers.get("Mcp-Session-Id")
        try:
            result = await _refresh_tools(
                request.app,
                request_id="playground-tools-list",
                protocol_version=request.headers.get("MCP-Protocol-Version"),
                session_id=session_id,
                correlation_id_value=correlation_id(request),
            )
            return JSONResponse({"items": result.get("tools", []), "next_cursor": result.get("nextCursor")})
        except DownstreamMcpError:
            return JSONResponse({"items": [], "next_cursor": None})

    @app.post("/v1/playground/tools/call")
    async def playground_tools_call(request: Request) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        exposed_name = body.get("exposed_name") or body.get("name")
        if not isinstance(exposed_name, str):
            return api_error("validation_failed", "exposed_name is required", status_code=422)
        payload = {
            "jsonrpc": "2.0",
            "id": body.get("request_id") or "playground-call",
            "method": "tools/call",
            "params": {"name": exposed_name, "arguments": body.get("arguments") if isinstance(body.get("arguments"), dict) else {}},
        }
        return JSONResponse(await _handle_tools_call(request.app, payload, request))

    @app.get("/v1/tool-invocations")
    async def list_tool_invocations(request: Request, limit: int = 20) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        items = await request.app.state.repository.recent_invocations(limit=max(1, min(limit, 100)))
        return JSONResponse({"items": items, "next_cursor": None})

    @app.get("/v1/audit-logs")
    async def list_audit_logs(
        request: Request,
        limit: int = 20,
        action: str | None = None,
        resource_type: str | None = None,
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        items = await request.app.state.repository.recent_audit_logs(
            limit=max(1, min(limit, 100)),
            action=action,
            resource_type=resource_type,
        )
        return JSONResponse({"items": items, "next_cursor": None})

    @app.get("/v1/jobs/{job_id}")
    async def get_job(request: Request, job_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        job = await request.app.state.repository.get_job(job_id)
        if job is None:
            return not_found("job")
        return JSONResponse(job)

    return app


app = create_app()
