from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from coremcp.api._schemas import (
    ServiceCredentialMasked,
    ServiceToolList,
    ToolOverrideList,
    ToolPresetResponse,
)
from coremcp.api.services_crud import include_service_crud_routes
from coremcp.credentials import mask_secret


def _credential_payload(body: dict[str, Any], *, secret_key: str = "secret", default_type: str = "bearer_token") -> tuple[str | None, str, str | None]:
    secret = body.get(secret_key)
    credential_type = body.get("credential_type")
    if not isinstance(credential_type, str):
        credential_type = default_type
    header_name = body.get("header_name") if isinstance(body.get("header_name"), str) else None
    return secret if isinstance(secret, str) else None, credential_type, header_name


def _credential_response(item: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"status": item["status"], "masked": item["masked_value"], "updated_at": item["updated_at"]})


async def _upsert_masked_credential(
    request: Request,
    *,
    service_id: str,
    secret: str,
    credential_type: str,
    header_name: str | None,
) -> dict[str, Any]:
    secret_ref = await request.app.state.vault.put(service_id=service_id, secret=secret)
    return await request.app.state.repos.credentials.upsert_service_credential(
        service_id=service_id,
        credential_type=credential_type,
        secret_ref=secret_ref,
        masked_value=mask_secret(secret),
        header_name=header_name,
    )


async def _rotate_masked_credential(
    request: Request,
    *,
    service_id: str,
    secret: str,
    credential_type: str,
    header_name: str | None,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    item = await _upsert_masked_credential(
        request,
        service_id=service_id,
        secret=secret,
        credential_type=credential_type,
        header_name=header_name if header_name is not None else (previous or {}).get("header_name"),
    )
    if previous:
        await request.app.state.vault.delete(previous["secret_ref"])
    return item


@dataclass(slots=True)
class ServicesRouteDeps:
    verify_admin_request: Callable[[Request], bool]
    unauthorized_response: Callable[..., JSONResponse]
    json_body: Callable[[Request], Awaitable[dict[str, Any] | JSONResponse]]
    api_error: Callable[..., JSONResponse]
    not_found: Callable[[str], JSONResponse]
    accepted: Callable[[dict[str, Any]], JSONResponse]
    request_ip: Callable[[Request], str | None]
    correlation_id: Callable[[Request], str]
    validate_service: Callable[..., Awaitable[dict[str, Any]]]
    validate_stdio_runtime_config: Callable[..., str | None]
    audit_stdio_command_rejected: Callable[..., Awaitable[None]]
    close_stdio_client_for_service: Callable[..., Awaitable[None]]
    forget_downstream_session: Callable[[FastAPI, str | None], None]
    publish_list_changed: Callable[..., Awaitable[None]]
    tool_preset_policy: Callable[[dict[str, Any], str], tuple[bool, str]]
    tool_override_counts: Callable[[list[dict[str, Any]]], dict[str, int]]
    string_list: Callable[[Any], list[str] | None]
    stdio_env: Callable[[Any], dict[str, str] | None]
    positive_int: Callable[[Any, int | None], int | None]
    stdio_default_idle_timeout: Callable[[Any], int | None]
    service_transport_types: set[str]
    tool_permission_levels: set[str]
    tool_presets: set[str]


def _include_service_tool_routes(router: APIRouter, deps: ServicesRouteDeps) -> None:
    @router.get("/v1/mcp-services/{service_id}/tools", response_model=ServiceToolList)
    async def service_tools(request: Request, service_id: str) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        if await request.app.state.repos.services.get_mcp_service(service_id) is None:
            return deps.not_found("mcp_service")
        items = await request.app.state.repos.catalog.list_service_tools(service_id)
        return JSONResponse({"items": items, "next_cursor": None})

    @router.get("/v1/mcp-services/{service_id}/tool-overrides", response_model=ToolOverrideList)
    async def service_tool_overrides(request: Request, service_id: str) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        if await request.app.state.repos.services.get_mcp_service(service_id) is None:
            return deps.not_found("mcp_service")
        items = await request.app.state.repos.toolbox.list_tool_overrides(service_id)
        return JSONResponse({"items": items, "next_cursor": None})

    @router.put("/v1/mcp-services/{service_id}/tool-overrides/{service_tool_id}")
    async def put_service_tool_override(request: Request, service_id: str, service_tool_id: str) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        body = await deps.json_body(request)
        if isinstance(body, JSONResponse):
            return body
        permission_level = str(body.get("permission_level") or "callable")
        if permission_level not in deps.tool_permission_levels:
            return deps.api_error("validation_failed", "permission_level must be hidden, visible_only, or callable", status_code=422)
        item = await request.app.state.repos.toolbox.upsert_tool_override(
            service_id=service_id,
            service_tool_id=service_tool_id,
            enabled=bool(body.get("enabled", True)),
            permission_level=permission_level,
        )
        if item is None:
            return deps.not_found("service_tool")
        await deps.publish_list_changed(
            request.app,
            reason="tool_permission.update",
            resource_id=service_tool_id,
            categories=("tools",),
        )
        return JSONResponse(item)

    @router.post("/v1/mcp-services/{service_id}/tool-overrides/preset", response_model=ToolPresetResponse)
    async def apply_service_tool_preset(request: Request, service_id: str) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        body = await deps.json_body(request)
        if isinstance(body, JSONResponse):
            return body
        preset = str(body.get("preset") or "")
        if preset not in deps.tool_presets:
            return deps.api_error("validation_failed", "preset must be readonly, full_access, or dangerous_off", status_code=422)
        if await request.app.state.repos.services.get_mcp_service(service_id) is None:
            return deps.not_found("mcp_service")

        tools = await request.app.state.repos.catalog.list_service_tools(service_id)
        items: list[dict[str, Any]] = []
        for tool in tools:
            enabled, permission_level = deps.tool_preset_policy(tool, preset)
            item = await request.app.state.repos.toolbox.upsert_tool_override(
                service_id=service_id,
                service_tool_id=tool["id"],
                enabled=enabled,
                permission_level=permission_level,
            )
            if item is not None:
                items.append(item)
        await request.app.state.repos.audit.log_audit(
            action="tool_permission.preset",
            resource_type="mcp_service",
            resource_id=service_id,
            metadata={"preset": preset, "counts": deps.tool_override_counts(items)},
            request_id=deps.correlation_id(request),
        )
        await deps.publish_list_changed(
            request.app,
            reason=f"tool_permission.preset.{preset}",
            resource_id=service_id,
            categories=("tools",),
        )
        return JSONResponse({"preset": preset, "items": items, "counts": deps.tool_override_counts(items), "next_cursor": None})



def _include_service_credential_routes(router: APIRouter, deps: ServicesRouteDeps) -> None:
    @router.put("/v1/mcp-services/{service_id}/credential")
    async def put_credential(request: Request, service_id: str) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        if await request.app.state.repos.services.get_mcp_service(service_id) is None:
            return deps.not_found("mcp_service")
        body = await deps.json_body(request)
        if isinstance(body, JSONResponse):
            return body
        secret, credential_type, header_name = _credential_payload(body)
        if not secret:
            return deps.api_error("validation_failed", "secret is required", status_code=422)
        item = await _upsert_masked_credential(
            request,
            service_id=service_id,
            secret=secret,
            credential_type=credential_type,
            header_name=header_name,
        )
        deps.forget_downstream_session(request.app, service_id)
        return _credential_response(item)

    @router.post("/v1/mcp-services/{service_id}/credential/rotate")
    async def rotate_credential(request: Request, service_id: str) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        if await request.app.state.repos.services.get_mcp_service(service_id) is None:
            return deps.not_found("mcp_service")
        body = await deps.json_body(request)
        if isinstance(body, JSONResponse):
            return body
        previous = await request.app.state.repos.credentials.get_service_credential(service_id)
        default_credential_type = previous["credential_type"] if previous else "bearer_token"
        secret, credential_type, header_name = _credential_payload(body, default_type=default_credential_type)
        if not secret:
            return deps.api_error("validation_failed", "secret is required", status_code=422)
        item = await _rotate_masked_credential(
            request,
            service_id=service_id,
            secret=secret,
            credential_type=credential_type,
            header_name=header_name,
            previous=previous,
        )
        await request.app.state.repos.audit.log_audit(
            action="credential.rotate",
            resource_type="mcp_service",
            resource_id=service_id,
            ip=deps.request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        deps.forget_downstream_session(request.app, service_id)
        return _credential_response(item)

    @router.get("/v1/mcp-services/{service_id}/credential", response_model=ServiceCredentialMasked)
    async def get_credential(request: Request, service_id: str) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        item = await request.app.state.repos.credentials.get_service_credential(service_id)
        if item is None:
            return JSONResponse({"status": "not_connected", "masked": None, "updated_at": None})
        return JSONResponse({"status": item["status"], "masked": item["masked_value"], "updated_at": item["updated_at"]})

    @router.delete("/v1/mcp-services/{service_id}/credential")
    async def delete_credential(request: Request, service_id: str) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        item = await request.app.state.repos.credentials.get_service_credential(service_id)
        if item:
            await request.app.state.vault.delete(item["secret_ref"])
        await request.app.state.repos.credentials.revoke_service_credential(service_id)
        await request.app.state.repos.services.update_mcp_service(service_id, {"status": "auth_required"})
        await deps.close_stdio_client_for_service(request.app, service_id)
        deps.forget_downstream_session(request.app, service_id)
        await deps.publish_list_changed(request.app, reason="credential.delete", resource_id=service_id)
        return deps.accepted({"service_id": service_id, "status": "not_connected"})



def register_services_routes(
    app: FastAPI,
    *,
    verify_admin_request: Callable[[Request], bool],
    unauthorized_response: Callable[..., JSONResponse],
    json_body: Callable[[Request], Awaitable[dict[str, Any] | JSONResponse]],
    api_error: Callable[..., JSONResponse],
    not_found: Callable[[str], JSONResponse],
    accepted: Callable[[dict[str, Any]], JSONResponse],
    request_ip: Callable[[Request], str | None],
    correlation_id: Callable[[Request], str],
    validate_service: Callable[..., Awaitable[dict[str, Any]]],
    validate_stdio_runtime_config: Callable[..., str | None],
    audit_stdio_command_rejected: Callable[..., Awaitable[None]],
    close_stdio_client_for_service: Callable[..., Awaitable[None]],
    forget_downstream_session: Callable[[FastAPI, str | None], None],
    publish_list_changed: Callable[..., Awaitable[None]],
    tool_preset_policy: Callable[[dict[str, Any], str], tuple[bool, str]],
    tool_override_counts: Callable[[list[dict[str, Any]]], dict[str, int]],
    string_list: Callable[[Any], list[str] | None],
    stdio_env: Callable[[Any], dict[str, str] | None],
    positive_int: Callable[[Any, int | None], int | None],
    stdio_default_idle_timeout: Callable[[Any], int | None],
    service_transport_types: set[str],
    tool_permission_levels: set[str],
    tool_presets: set[str],
) -> None:
    router = APIRouter(tags=["mcp-services"])
    deps = ServicesRouteDeps(
        verify_admin_request=verify_admin_request,
        unauthorized_response=unauthorized_response,
        json_body=json_body,
        api_error=api_error,
        not_found=not_found,
        accepted=accepted,
        request_ip=request_ip,
        correlation_id=correlation_id,
        validate_service=validate_service,
        validate_stdio_runtime_config=validate_stdio_runtime_config,
        audit_stdio_command_rejected=audit_stdio_command_rejected,
        close_stdio_client_for_service=close_stdio_client_for_service,
        forget_downstream_session=forget_downstream_session,
        publish_list_changed=publish_list_changed,
        tool_preset_policy=tool_preset_policy,
        tool_override_counts=tool_override_counts,
        string_list=string_list,
        stdio_env=stdio_env,
        positive_int=positive_int,
        stdio_default_idle_timeout=stdio_default_idle_timeout,
        service_transport_types=service_transport_types,
        tool_permission_levels=tool_permission_levels,
        tool_presets=tool_presets,
    )
    include_service_crud_routes(router, deps)
    _include_service_tool_routes(router, deps)
    _include_service_credential_routes(router, deps)
    app.include_router(router)
