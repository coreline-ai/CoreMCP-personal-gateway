from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from coremcp.auth import ClientTokenService, OAuthError, OAuthService, extract_bearer_token, hash_token, verify_admin_bearer
from coremcp.credentials import build_vault, mask_secret
from coremcp.db import DEFAULT_TOOLBOX_ID, Repository
from coremcp.logging import configure_logging
from coremcp.mcp_gateway import IdempotencyCache, ListChangedEventBus, SessionStore, negotiate_protocol_version
from coremcp.proxy import (
    DownstreamMcpClient,
    DownstreamMcpError,
    DownstreamTimeoutError,
    DownstreamToolError,
    UrlSafetyChecker,
    UrlSafetyError,
)
from coremcp.registry.catalog import catalog_row_to_mcp_tool, normalize_downstream_tools, slugify_tool_name
from coremcp.settings import Settings, get_settings

SERVER_CAPABILITIES = {"tools": {"listChanged": True}}
JSONRPC_VERSION = "2.0"
ONE_TIME_TOKEN_PREFIX = "cmcp_otk_"
ONE_TIME_TOKEN_TTL_SECONDS = 600
DEFAULT_CLIENT_SCOPES = ["mcp:tools.read", "mcp:tools.call"]
ALLOWED_CLIENT_SCOPES = {"mcp:tools.read", "mcp:tools.call", "mcp:connections.manage"}
TOOL_PERMISSION_LEVELS = {"hidden", "visible_only", "callable"}
TOOL_PRESETS = {"readonly", "full_access", "dangerous_off"}
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
        claims = request.app.state.oauth.verify_access_token(
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


def tool_error_result(error_code: str, message: str, *, downstream_code: int | None = None, reason: str | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"error_code": error_code}
    if downstream_code is not None:
        meta["downstream_code"] = downstream_code
    if reason is not None:
        meta["reason"] = reason
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
        "_meta": {"coremcp": meta},
    }


def _is_future_protocol(value: str | None) -> bool:
    return bool(value and value > "2025-11-25")


def _normalize_downstream_tool(tool: dict[str, Any]) -> tuple[dict[str, Any], str]:
    original_name = str(tool.get("name", "")).strip()
    exposed_name = original_name if "." in original_name else f"fake.{original_name}"
    normalized = dict(tool)
    normalized["name"] = exposed_name
    return normalized, original_name


def _get_request_id(payload: dict[str, Any]) -> Any:
    return payload.get("id")


def _idempotency_cache_key(request: Request, exposed_name: str) -> str | None:
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None
    bearer_kind = "client" if getattr(request.state, "client_auth", None) is not None else "admin"
    connection_id = getattr(getattr(request.state, "client_auth", None), "external_connection_id", "admin")
    return f"{bearer_kind}:{connection_id}:{exposed_name}:{key}"


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
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _oauth_error_response(exc: OAuthError) -> JSONResponse:
    return JSONResponse({"error": exc.code, "error_description": str(exc)}, status_code=exc.status_code)


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


async def _publish_list_changed(app: FastAPI, *, reason: str, resource_id: str | None = None) -> None:
    app.state.tool_registry = {}
    await app.state.list_changed_bus.publish_list_changed(reason=reason, resource_id=resource_id)


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
                "service_id": row["service_id"],
                "service_tool_id": row["service_tool_id"],
                "override_enabled": row.get("override_enabled", 1),
                "permission_level": row.get("permission_level", "callable"),
            }
            if bool(row.get("override_enabled", 1)) and row.get("permission_level", "callable") != "hidden":
                tools.append(tool)
        app.state.tool_registry = registry
        return {"tools": tools, "nextCursor": None}

    downstream: DownstreamMcpClient = app.state.downstream
    response = await downstream.request(
        method="tools/list",
        params=params or {},
        request_id=request_id,
        protocol_version=protocol_version,
        session_id=session_id,
        correlation_id=correlation_id_value,
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
            "service_id": None,
            "service_tool_id": None,
        }

    app.state.tool_registry = registry
    result = dict(result)
    result["tools"] = transformed_tools
    result.setdefault("nextCursor", None)
    return result


async def _handle_initialize(
    app: FastAPI,
    payload: dict[str, Any],
    request: Request,
) -> tuple[dict[str, Any], str]:
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    requested = params.get("protocolVersion") or request.headers.get("MCP-Protocol-Version")
    protocol_version = negotiate_protocol_version(requested)
    session = app.state.sessions.create(protocol_version)

    downstream_params = dict(params)
    downstream_params["protocolVersion"] = protocol_version
    try:
        await app.state.downstream.request(
            method="initialize",
            params=downstream_params,
            request_id=_get_request_id(payload),
            protocol_version=protocol_version,
            session_id=session.id,
            correlation_id=correlation_id(request),
        )
    except DownstreamMcpError:
        # P0 keeps CoreMCP usable even if the fake downstream is not started yet.
        pass

    result = {
        "protocolVersion": protocol_version,
        "capabilities": SERVER_CAPABILITIES,
        "serverInfo": {"name": "CoreMCP", "version": app.state.settings.app_version},
    }
    if _is_future_protocol(requested):
        result["_coremcp"] = {"warning": "future protocol downgraded to latest supported version"}
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

    downstream_params = dict(params)
    downstream_params["name"] = route["original_name"]
    try:
        checker = UrlSafetyChecker(app.state.settings)
        safety_result = checker.assert_safe(route["endpoint_url"])
        downstream_headers = await _downstream_headers_for_service(app, route.get("service_id"))
        inflight_key = str(request_id)
        app.state.inflight_downstream_calls[inflight_key] = {
            "url": route["endpoint_url"],
            "service_id": route.get("service_id"),
            "session_id": session_id,
            "protocol_version": protocol_version,
            "downstream_headers": downstream_headers,
        }
        downstream_response = await app.state.downstream.request(
            method="tools/call",
            params=downstream_params,
            request_id=request_id,
            protocol_version=protocol_version,
            session_id=session_id,
            url=route["endpoint_url"],
            downstream_headers=downstream_headers,
            url_safety_checker=checker,
            safety_result=safety_result,
            correlation_id=correlation_id(request),
        )
        app.state.inflight_downstream_calls.pop(inflight_key, None)
        result = downstream_response.get("result")
        if not isinstance(result, dict):
            raise DownstreamMcpError("downstream tools/call returned invalid result")
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
    return jsonrpc_error(request_id, -32601, "Method not found"), None


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
    try:
        await repository.update_mcp_service(service_id, {"status": "validating"})
        if job_id:
            await repository.update_job(job_id, status="running", progress=0.2)

        checker = UrlSafetyChecker(app.state.settings)
        safety_result = checker.assert_safe(service["endpoint_url"])
        stages.append({"name": "url_safety_check", "status": "success"})

        init_response = await app.state.downstream.request(
            method="initialize",
            params={"protocolVersion": protocol_version, "capabilities": {}, "clientInfo": {"name": "coremcp-validator", "version": app.state.settings.app_version}},
            request_id=f"validate-{service_id}-init",
            protocol_version=protocol_version,
            url=service["endpoint_url"],
            downstream_headers=await _downstream_headers_for_service(app, service_id),
            url_safety_checker=checker,
            safety_result=safety_result,
            correlation_id=correlation_id_value,
        )
        init_result = init_response.get("result") if isinstance(init_response, dict) else {}
        if isinstance(init_result, dict) and init_result.get("protocolVersion"):
            protocol_version = str(init_result["protocolVersion"])
        stages.append({"name": "mcp_initialize", "status": "success"})
        if job_id:
            await repository.update_job(job_id, status="running", progress=0.5)

        tools_response = await app.state.downstream.request(
            method="tools/list",
            params={},
            request_id=f"validate-{service_id}-tools",
            protocol_version=protocol_version,
            url=service["endpoint_url"],
            downstream_headers=await _downstream_headers_for_service(app, service_id),
            url_safety_checker=checker,
            safety_result=safety_result,
            correlation_id=correlation_id_value,
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
        stages.append({"name": "metadata_scan", "status": "success", "warnings": warnings, **change_summary})

        summary = {
            "stages": stages,
            "tools_found": len(saved),
            "warnings": warnings,
            "schema_drift": change_summary,
            "schema_diff": schema_diff["details"],
        }
        await repository.mark_service_validated(
            service_id=service_id,
            status="active",
            protocol_version=protocol_version,
            summary=summary,
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
        app.state.vault = build_vault(settings)
        await app.state.repository.connect()
        app.state.oauth = OAuthService(settings, app.state.repository, http_client)
        await app.state.vault.is_ready()
        try:
            yield
        finally:
            await app.state.repository.close()
            if owns_http_client:
                await http_client.aclose()

    app = FastAPI(title="CoreMCP API", version=settings.app_version, lifespan=lifespan)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID")
        request_id = incoming.strip() if incoming and incoming.strip() else f"req_{secrets.token_hex(16)}"
        request.state.request_id = request_id
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
            body = await _form_or_json_body(request)
            client = request.app.state.oauth.register_client(body)
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
                )
            elif grant_type == "refresh_token":
                payload = await request.app.state.oauth.refresh(
                    refresh_token=str(body.get("refresh_token") or ""),
                    client_id=str(body.get("client_id") or ""),
                    resource=resource,
                    issuer=oauth_issuer(request),
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
        body = await _form_or_json_body(request)
        await request.app.state.oauth.revoke(str(body.get("token") or ""))
        return JSONResponse({"revoked": True}, headers={"Cache-Control": "no-store"})

    @app.post("/oauth/introspect")
    async def oauth_introspect(request: Request) -> Response:
        if request.app.state.settings.auth_mode != "oauth":
            return Response(status_code=404)
        body = await _form_or_json_body(request)
        payload = await request.app.state.oauth.introspect(
            str(body.get("token") or ""),
            issuer=oauth_issuer(request),
            audience=oauth_resource(request),
        )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

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
        if not isinstance(payload, dict):
            return JSONResponse(jsonrpc_error(None, -32600, "Invalid Request"), status_code=400)

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

        async def events():
            subscription = await request.app.state.list_changed_bus.subscribe()
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
        endpoint_url = body.get("endpoint_url")
        if not isinstance(name, str) or not name.strip() or not isinstance(endpoint_url, str) or not endpoint_url.strip():
            return api_error("validation_failed", "name and endpoint_url are required", status_code=422)
        slug = body.get("slug") if isinstance(body.get("slug"), str) and body.get("slug") else slugify_tool_name(name).lower()
        try:
            service = await request.app.state.repository.create_mcp_service(
                name=name.strip(),
                slug=slug,
                endpoint_url=endpoint_url.strip(),
                auth_type=body.get("auth_type") if isinstance(body.get("auth_type"), str) else "none",
                description=body.get("description") if isinstance(body.get("description"), str) else None,
                category=body.get("category") if isinstance(body.get("category"), str) else None,
                logo_url=body.get("logo_url") if isinstance(body.get("logo_url"), str) else None,
                homepage_url=body.get("homepage_url") if isinstance(body.get("homepage_url"), str) else None,
                documentation_url=body.get("documentation_url") if isinstance(body.get("documentation_url"), str) else None,
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
            )
            if key in body
        }
        service = await request.app.state.repository.update_mcp_service(service_id, updates)
        if service is None:
            return not_found("mcp_service")
        await _publish_list_changed(request.app, reason="service.update", resource_id=service_id)
        return JSONResponse(service)

    @app.delete("/v1/mcp-services/{service_id}")
    async def delete_service(request: Request, service_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        await request.app.state.repository.soft_delete_mcp_service(service_id)
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
        await _publish_list_changed(request.app, reason="tool_permission.update", resource_id=service_tool_id)
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
        await _publish_list_changed(request.app, reason=f"tool_permission.preset.{preset}", resource_id=service_id)
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
        await _publish_list_changed(request.app, reason="toolbox_item.upsert", resource_id=item.get("id"))
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
        await _publish_list_changed(request.app, reason="toolbox_item.update", resource_id=item_id)
        return JSONResponse(item)

    @app.delete("/v1/toolboxes/{toolbox_id}/items/{item_id}")
    async def delete_toolbox_item(request: Request, toolbox_id: str, item_id: str) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        await request.app.state.repository.delete_toolbox_item(item_id)
        await _publish_list_changed(request.app, reason="toolbox_item.delete", resource_id=item_id)
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
