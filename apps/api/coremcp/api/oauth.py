from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from coremcp.api._schemas import (
    JWKSResponse,
    OAuthAuthorizationServerMetadata,
    OAuthProtectedResourceMetadata,
)
from coremcp.api.dependencies import get_oauth_dcr_rate_limiter, get_oauth_service, get_settings
from coremcp.auth import OAuthError
from coremcp.auth.oauth import OAuthService
from coremcp.auth.rate_limit import FixedWindowRateLimiter
from coremcp.settings import Settings


def oauth_issuer(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def oauth_resource(request: Request) -> str:
    return str(request.url_for("mcp"))


async def form_or_json_body(request: Request) -> dict[str, Any]:
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


def oauth_error_response(exc: OAuthError) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if exc.retry_after_seconds is not None:
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return JSONResponse(
        {"error": exc.code, "error_description": str(exc)},
        status_code=exc.status_code,
        headers=headers,
    )


def oauth_dcr_policy_error(settings_obj: Settings) -> OAuthError | None:
    if settings_obj.oauth_dcr_enabled:
        return None
    return OAuthError(
        "access_denied",
        "OAuth dynamic client registration is disabled by CoreMCP policy",
        status_code=403,
    )


def oauth_allowed_client_ids(settings_obj: Settings) -> set[str]:
    return settings_obj.oauth_allowed_client_id_set


def oauth_client_allowlist_policy_error(settings_obj: Settings, client_id: str) -> OAuthError | None:
    allowed_client_ids = oauth_allowed_client_ids(settings_obj)
    if not allowed_client_ids or client_id in allowed_client_ids:
        return None
    return OAuthError(
        "unauthorized_client",
        "OAuth client_id is not allowed by CoreMCP policy",
        status_code=403,
    )


def check_oauth_dcr_rate_limit(
    request: Request,
    *,
    limiter: FixedWindowRateLimiter,
    request_ip: Callable[[Request], str | None],
    limit: int,
    window_seconds: int,
) -> OAuthError | None:
    decision = limiter.check(
        f"oauth:dcr:{request_ip(request) or 'unknown'}",
        limit=limit,
        window_seconds=window_seconds,
    )
    if decision.allowed:
        return None
    return OAuthError(
        "rate_limited",
        "OAuth dynamic client registration rate limit exceeded",
        status_code=429,
        retry_after_seconds=decision.retry_after_seconds,
    )


def register_oauth_routes(
    app: FastAPI,
    *,
    request_ip: Callable[[Request], str | None],
    dcr_rate_limit: int,
    dcr_rate_limit_window_seconds: int,
) -> None:
    @app.get("/.well-known/oauth-protected-resource", response_model=OAuthProtectedResourceMetadata)
    async def protected_resource_metadata(
        request: Request,
        settings_obj: Settings = Depends(get_settings),
    ) -> Response:
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

    @app.get("/.well-known/oauth-authorization-server", response_model=OAuthAuthorizationServerMetadata)
    async def authorization_server_metadata(
        request: Request,
        settings_obj: Settings = Depends(get_settings),
    ) -> Response:
        if settings_obj.auth_mode != "oauth":
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

    @app.get("/.well-known/jwks.json", response_model=JWKSResponse)
    async def jwks(
        request: Request,
        settings_obj: Settings = Depends(get_settings),
        oauth_service: OAuthService = Depends(get_oauth_service),
    ) -> Response:
        if settings_obj.auth_mode != "oauth":
            return Response(status_code=404)
        return JSONResponse(oauth_service.jwks(), headers={"Cache-Control": "no-store"})

    @app.post("/oauth/register")
    async def oauth_register(
        request: Request,
        settings_obj: Settings = Depends(get_settings),
        oauth_service: OAuthService = Depends(get_oauth_service),
        dcr_limiter: FixedWindowRateLimiter = Depends(get_oauth_dcr_rate_limiter),
    ) -> Response:
        if settings_obj.auth_mode != "oauth":
            return Response(status_code=404)
        try:
            if policy_error := oauth_dcr_policy_error(settings_obj):
                return oauth_error_response(policy_error)
            if limited := check_oauth_dcr_rate_limit(
                request,
                limiter=dcr_limiter,
                request_ip=request_ip,
                limit=dcr_rate_limit,
                window_seconds=dcr_rate_limit_window_seconds,
            ):
                return oauth_error_response(limited)
            body = await form_or_json_body(request)
            client = await oauth_service.register_client(body)
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
            return oauth_error_response(exc)

    @app.get("/oauth/authorize")
    async def oauth_authorize(
        request: Request,
        settings_obj: Settings = Depends(get_settings),
        oauth_service: OAuthService = Depends(get_oauth_service),
    ) -> Response:
        if settings_obj.auth_mode != "oauth":
            return Response(status_code=404)
        query = request.query_params
        try:
            client_id = str(query.get("client_id") or "")
            if policy_error := oauth_client_allowlist_policy_error(settings_obj, client_id):
                return oauth_error_response(policy_error)
            if query.get("response_type") != "code":
                raise OAuthError("unsupported_response_type", "only response_type=code is supported")
            resource = query.get("resource") or oauth_resource(request)
            if resource != oauth_resource(request):
                raise OAuthError("invalid_target", "resource must match CoreMCP /mcp")
            code = await oauth_service.create_authorization_code(
                client_id=client_id,
                redirect_uri=str(query.get("redirect_uri") or ""),
                resource=resource,
                scope=str(query.get("scope") or "mcp:tools.read mcp:tools.call"),
                code_challenge=str(query.get("code_challenge") or ""),
                code_challenge_method=str(query.get("code_challenge_method") or ""),
                client_ip=request_ip(request),
            )
            location = oauth_service.redirect_with_code(
                str(query.get("redirect_uri")),
                code=code,
                state=query.get("state"),
            )
            return RedirectResponse(location, status_code=302)
        except OAuthError as exc:
            return oauth_error_response(exc)

    @app.post("/oauth/token")
    async def oauth_token(
        request: Request,
        settings_obj: Settings = Depends(get_settings),
        oauth_service: OAuthService = Depends(get_oauth_service),
    ) -> Response:
        if settings_obj.auth_mode != "oauth":
            return Response(status_code=404)
        try:
            body = await form_or_json_body(request)
            client_id = str(body.get("client_id") or "")
            if policy_error := oauth_client_allowlist_policy_error(settings_obj, client_id):
                return oauth_error_response(policy_error)
            grant_type = body.get("grant_type")
            resource = str(body.get("resource") or oauth_resource(request))
            if resource != oauth_resource(request):
                raise OAuthError("invalid_target", "resource must match CoreMCP /mcp")
            if grant_type == "authorization_code":
                payload = await oauth_service.exchange_authorization_code(
                    code=str(body.get("code") or ""),
                    client_id=client_id,
                    redirect_uri=str(body.get("redirect_uri") or ""),
                    code_verifier=str(body.get("code_verifier") or ""),
                    resource=resource,
                    issuer=oauth_issuer(request),
                    client_ip=request_ip(request),
                )
            elif grant_type == "refresh_token":
                payload = await oauth_service.refresh(
                    refresh_token=str(body.get("refresh_token") or ""),
                    client_id=client_id,
                    resource=resource,
                    issuer=oauth_issuer(request),
                    client_ip=request_ip(request),
                )
            else:
                raise OAuthError("unsupported_grant_type", "grant_type must be authorization_code or refresh_token")
            return JSONResponse(payload, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
        except OAuthError as exc:
            return oauth_error_response(exc)

    @app.post("/oauth/revoke")
    async def oauth_revoke(
        request: Request,
        settings_obj: Settings = Depends(get_settings),
        oauth_service: OAuthService = Depends(get_oauth_service),
    ) -> Response:
        if settings_obj.auth_mode != "oauth":
            return Response(status_code=404)
        try:
            body = await form_or_json_body(request)
            await oauth_service.revoke(str(body.get("token") or ""))
            return JSONResponse({"revoked": True}, headers={"Cache-Control": "no-store"})
        except OAuthError as exc:
            return oauth_error_response(exc)

    @app.post("/oauth/introspect")
    async def oauth_introspect(
        request: Request,
        settings_obj: Settings = Depends(get_settings),
        oauth_service: OAuthService = Depends(get_oauth_service),
    ) -> Response:
        if settings_obj.auth_mode != "oauth":
            return Response(status_code=404)
        try:
            body = await form_or_json_body(request)
            payload = await oauth_service.introspect(
                str(body.get("token") or ""),
                issuer=oauth_issuer(request),
                audience=oauth_resource(request),
            )
            return JSONResponse(payload, headers={"Cache-Control": "no-store"})
        except OAuthError as exc:
            return oauth_error_response(exc)
