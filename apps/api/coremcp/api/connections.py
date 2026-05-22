from __future__ import annotations

import sqlite3
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from coremcp.api._schemas import ClientTokenList, ExternalConnectionList
from coremcp.api.dependencies import get_repos
from coremcp.auth import ClientTokenService, hash_token
from coremcp.db import DEFAULT_TOOLBOX_ID
from coremcp.db.repository_facade import RepositoryFacades


def register_connections_routes(
    app: FastAPI,
    *,
    verify_admin_request: Callable[[Request], bool],
    unauthorized_response: Callable[..., JSONResponse],
    json_body: Callable[[Request], Awaitable[dict[str, Any] | JSONResponse]],
    api_error: Callable[..., JSONResponse],
    accepted: Callable[[dict[str, Any]], JSONResponse],
    request_ip: Callable[[Request], str | None],
    validated_scopes: Callable[[Any], list[str] | None],
    generate_one_time_token: Callable[[], str],
    utc_sql_timestamp: Callable[[float | None], str],
    iso_z: Callable[[str | None], str | None],
    connection_token_prompt: Callable[[str, str | None], str],
    one_time_token_prefix: str,
    one_time_token_ttl_seconds: int,
) -> None:
    @app.get("/v1/settings/client-tokens", response_model=ClientTokenList)
    async def list_client_tokens(
        request: Request,
        limit: int = 50,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        items = await repos.credentials.list_personal_access_tokens(limit=max(1, min(limit, 100)))
        return JSONResponse({"items": items, "next_cursor": None})

    @app.post("/v1/settings/client-tokens")
    async def issue_client_token(
        request: Request,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await json_body(request)
        if isinstance(body, JSONResponse):
            return body
        external_connection_id = body.get("external_connection_id")
        if not isinstance(external_connection_id, str):
            return api_error("validation_failed", "external_connection_id is required", status_code=422)
        scopes = validated_scopes(body.get("scopes"))
        if scopes is None:
            return api_error("validation_failed", "scopes must be supported MCP scopes", status_code=422)
        try:
            item = await ClientTokenService(repos.credentials).issue(
                external_connection_id=external_connection_id,
                scopes=scopes,
                protocol_version=body.get("protocol_version") if isinstance(body.get("protocol_version"), str) else None,
            )
        except ValueError as exc:
            return api_error("validation_failed", str(exc), status_code=422)
        return JSONResponse(item, status_code=201)

    @app.delete("/v1/settings/client-tokens/{token_id}")
    async def revoke_client_token(
        request: Request,
        token_id: str,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        await ClientTokenService(repos.credentials).revoke(token_id)
        return accepted({"id": token_id, "status": "revoked"})

    @app.post("/v1/external-connections/one-time-token")
    async def create_one_time_connection_token(
        request: Request,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await json_body(request)
        if isinstance(body, JSONResponse):
            return body
        scopes = validated_scopes(body.get("requested_scopes", body.get("scopes")))
        if scopes is None:
            return api_error("validation_failed", "requested_scopes must be supported MCP scopes", status_code=422)
        client_type = str(body.get("client_type") or "openclaw")
        toolbox_id = body.get("toolbox_id") if isinstance(body.get("toolbox_id"), str) else DEFAULT_TOOLBOX_ID
        token = generate_one_time_token()
        expires_at = utc_sql_timestamp(time.time() + one_time_token_ttl_seconds)
        try:
            record = await repos.credentials.create_connection_token(
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
        expires_at_iso = iso_z(record["expires_at"])
        return JSONResponse(
            {
                "token": token,
                "token_type": "coremcp_one_time",
                "expires_in": one_time_token_ttl_seconds,
                "expires_at": expires_at_iso,
                "client_type": record["client_type"],
                "toolbox_id": record["toolbox_id"],
                "requested_scopes": record["requested_scopes"],
                "connection_prompt": connection_token_prompt(token, expires_at_iso),
            },
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/v1/external-connections/exchange")
    async def exchange_one_time_connection_token(
        request: Request,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        body = await json_body(request)
        if isinstance(body, JSONResponse):
            return body
        presented = body.get("one_time_token") or body.get("token")
        if not isinstance(presented, str) or not presented.startswith(one_time_token_prefix):
            return api_error("invalid_token", "one-time token is invalid or expired", status_code=401)
        token_record = await repos.credentials.consume_connection_token(
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
            connection = await repos.connections.create_external_connection(
                client_type=token_record["client_type"],
                client_name=client_name,
                toolbox_id=token_record["toolbox_id"],
                protocol_version=protocol_version,
                scopes=token_record["requested_scopes"],
                created_ip=request_ip(request),
                created_user_agent=request.headers.get("user-agent"),
            )
            issued = await ClientTokenService(repos.credentials).issue(
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

    @app.get("/v1/external-connections", response_model=ExternalConnectionList)
    async def list_external_connections(
        request: Request,
        limit: int = 50,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        items = await repos.connections.list_external_connections(limit=max(1, min(limit, 100)))
        return JSONResponse({"items": items, "next_cursor": None})

    @app.post("/v1/external-connections")
    async def create_external_connection(
        request: Request,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await json_body(request)
        if isinstance(body, JSONResponse):
            return body
        client_type = str(body.get("client_type") or "other")
        client_name = body.get("client_name") if isinstance(body.get("client_name"), str) else client_type
        try:
            item = await repos.connections.create_external_connection(
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
    async def revoke_external_connection(
        request: Request,
        connection_id: str,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        await repos.connections.revoke_external_connection(connection_id)
        return accepted({"id": connection_id, "status": "revoked"})
