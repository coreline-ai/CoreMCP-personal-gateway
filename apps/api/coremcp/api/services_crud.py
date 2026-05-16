from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from coremcp.credentials import mask_secret
from coremcp.proxy import DownstreamMcpError, UrlSafetyError
from coremcp.registry.catalog import slugify_tool_name


SERVICE_UPDATE_FIELDS = (
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


def _bounded_service_limit(limit: int) -> int:
    return max(1, min(limit, 100))


def _service_slug(body: dict[str, Any], name: str) -> str:
    return body.get("slug") if isinstance(body.get("slug"), str) and body.get("slug") else slugify_tool_name(name).lower()


def _service_stdio_command(body: dict[str, Any]) -> str | None:
    return body.get("stdio_command") if isinstance(body.get("stdio_command"), str) else None


async def _validate_stdio_runtime_for_body(
    request: Request,
    *,
    command: str | None,
    cwd: str | None,
    validate_stdio_runtime_config: Callable[..., str | None],
    audit_stdio_command_rejected: Callable[..., Awaitable[None]],
    api_error: Callable[..., JSONResponse],
    service_id: str | None = None,
) -> JSONResponse | None:
    runtime_error = validate_stdio_runtime_config(
        command if isinstance(command, str) else None,
        cwd if isinstance(cwd, str) else None,
        settings=request.app.state.settings,
    )
    if runtime_error is None:
        return None
    await audit_stdio_command_rejected(
        request,
        command=command if isinstance(command, str) else None,
        reason=runtime_error,
        service_id=service_id,
    )
    return api_error("validation_failed", runtime_error, status_code=422)


async def _service_create_payload(
    request: Request,
    body: dict[str, Any],
    *,
    validate_stdio_runtime_config: Callable[..., str | None],
    audit_stdio_command_rejected: Callable[..., Awaitable[None]],
    api_error: Callable[..., JSONResponse],
    string_list: Callable[[Any], list[str] | None],
    stdio_env: Callable[[Any], dict[str, str] | None],
    positive_int: Callable[[Any, int | None], int | None],
    stdio_default_idle_timeout: Callable[[Any], int | None],
    service_transport_types: set[str],
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, api_error("validation_failed", "name is required", status_code=422)

    transport_type = body.get("transport_type") if isinstance(body.get("transport_type"), str) else "http"
    if transport_type not in service_transport_types:
        return None, api_error("validation_failed", "transport_type must be http or stdio", status_code=422)

    endpoint_url = body.get("endpoint_url")
    slug = _service_slug(body, name)
    stdio_command = _service_stdio_command(body)
    if transport_type == "http":
        if not isinstance(endpoint_url, str) or not endpoint_url.strip():
            return None, api_error("validation_failed", "endpoint_url is required for http transport", status_code=422)
        endpoint_value = endpoint_url.strip()
    else:
        if not stdio_command or not stdio_command.strip():
            return None, api_error("validation_failed", "stdio_command is required for stdio transport", status_code=422)
        if runtime_response := await _validate_stdio_runtime_for_body(
            request,
            command=stdio_command,
            cwd=body.get("stdio_cwd") if isinstance(body.get("stdio_cwd"), str) else None,
            validate_stdio_runtime_config=validate_stdio_runtime_config,
            audit_stdio_command_rejected=audit_stdio_command_rejected,
            api_error=api_error,
        ):
            return None, runtime_response
        endpoint_value = endpoint_url.strip() if isinstance(endpoint_url, str) and endpoint_url.strip() else f"stdio://{slug}"

    return (
        {
            "name": name.strip(),
            "slug": slug,
            "endpoint_url": endpoint_value,
            "auth_type": body.get("auth_type") if isinstance(body.get("auth_type"), str) else "none",
            "description": body.get("description") if isinstance(body.get("description"), str) else None,
            "category": body.get("category") if isinstance(body.get("category"), str) else None,
            "logo_url": body.get("logo_url") if isinstance(body.get("logo_url"), str) else None,
            "homepage_url": body.get("homepage_url") if isinstance(body.get("homepage_url"), str) else None,
            "documentation_url": body.get("documentation_url") if isinstance(body.get("documentation_url"), str) else None,
            "transport_type": transport_type,
            "stdio_command": stdio_command.strip() if stdio_command else None,
            "stdio_args": string_list(body.get("stdio_args")),
            "stdio_env": stdio_env(body.get("stdio_env")),
            "stdio_cwd": body.get("stdio_cwd") if isinstance(body.get("stdio_cwd"), str) else None,
            "stdio_idle_timeout_seconds": positive_int(
                body.get("stdio_idle_timeout_seconds"),
                stdio_default_idle_timeout(request.app.state.settings),
            ),
        },
        None,
    )


async def _service_update_payload(
    request: Request,
    service_id: str,
    body: dict[str, Any],
    *,
    validate_stdio_runtime_config: Callable[..., str | None],
    audit_stdio_command_rejected: Callable[..., Awaitable[None]],
    api_error: Callable[..., JSONResponse],
    string_list: Callable[[Any], list[str] | None],
    stdio_env: Callable[[Any], dict[str, str] | None],
    positive_int: Callable[[Any, int | None], int | None],
    stdio_default_idle_timeout: Callable[[Any], int | None],
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    updates = {key: body[key] for key in SERVICE_UPDATE_FIELDS if key in body}
    if updates.get("transport_type") not in {None, "http", "stdio"}:
        return None, api_error("validation_failed", "transport_type must be http or stdio", status_code=422)
    if "stdio_args" in updates:
        updates["stdio_args"] = string_list(updates["stdio_args"])
    if "stdio_env" in updates:
        updates["stdio_env"] = stdio_env(updates["stdio_env"])
    if "stdio_idle_timeout_seconds" in updates:
        updates["stdio_idle_timeout_seconds"] = positive_int(
            updates["stdio_idle_timeout_seconds"],
            stdio_default_idle_timeout(request.app.state.settings),
        )
    if updates.get("transport_type") == "stdio" or "stdio_command" in updates or "stdio_cwd" in updates:
        current = await request.app.state.repos.services.get_mcp_service(service_id)
        command = updates.get("stdio_command", current.get("stdio_command") if current else None)
        cwd = updates.get("stdio_cwd", current.get("stdio_cwd") if current else None)
        if updates.get("transport_type", current.get("transport_type") if current else None) == "stdio":
            if runtime_response := await _validate_stdio_runtime_for_body(
                request,
                command=command if isinstance(command, str) else None,
                cwd=cwd if isinstance(cwd, str) else None,
                validate_stdio_runtime_config=validate_stdio_runtime_config,
                audit_stdio_command_rejected=audit_stdio_command_rejected,
                api_error=api_error,
                service_id=service_id,
            ):
                return None, runtime_response
    return updates, None


def include_service_crud_routes(router: APIRouter, deps: Any) -> None:
    @router.get("/v1/mcp-services")
    async def list_services(request: Request, limit: int = 50, status: str | None = None) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        items = await request.app.state.repos.services.list_mcp_services(limit=_bounded_service_limit(limit), status=status)
        return JSONResponse({"items": items, "next_cursor": None})

    @router.post("/v1/mcp-services")
    async def create_service(request: Request) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        body = await deps.json_body(request)
        if isinstance(body, JSONResponse):
            return body
        create_payload, error_response = await _service_create_payload(
            request,
            body,
            validate_stdio_runtime_config=deps.validate_stdio_runtime_config,
            audit_stdio_command_rejected=deps.audit_stdio_command_rejected,
            api_error=deps.api_error,
            string_list=deps.string_list,
            stdio_env=deps.stdio_env,
            positive_int=deps.positive_int,
            stdio_default_idle_timeout=deps.stdio_default_idle_timeout,
            service_transport_types=deps.service_transport_types,
        )
        if error_response is not None or create_payload is None:
            assert error_response is not None
            return error_response
        try:
            service = await request.app.state.repos.services.create_mcp_service(**create_payload)
        except sqlite3.IntegrityError as exc:
            return deps.api_error("conflict", "service slug already exists", status_code=409, details=str(exc))
        credential = body.get("credential")
        if isinstance(credential, dict) and isinstance(credential.get("value"), str):
            secret_ref = await request.app.state.vault.put(service_id=service["id"], secret=credential["value"])
            await request.app.state.repos.credentials.upsert_service_credential(
                service_id=service["id"],
                credential_type=str(credential.get("type") or service.get("auth_type") or "bearer_token"),
                secret_ref=secret_ref,
                masked_value=mask_secret(credential["value"]),
                header_name=credential.get("header_name") if isinstance(credential.get("header_name"), str) else None,
            )
        return JSONResponse(service, status_code=201)

    @router.get("/v1/mcp-services/{service_id}")
    async def get_service(request: Request, service_id: str) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        service = await request.app.state.repos.services.get_mcp_service(service_id)
        if service is None:
            return deps.not_found("mcp_service")
        return JSONResponse(service)

    @router.patch("/v1/mcp-services/{service_id}")
    async def patch_service(request: Request, service_id: str) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        body = await deps.json_body(request)
        if isinstance(body, JSONResponse):
            return body
        updates, error_response = await _service_update_payload(
            request,
            service_id,
            body,
            validate_stdio_runtime_config=deps.validate_stdio_runtime_config,
            audit_stdio_command_rejected=deps.audit_stdio_command_rejected,
            api_error=deps.api_error,
            string_list=deps.string_list,
            stdio_env=deps.stdio_env,
            positive_int=deps.positive_int,
            stdio_default_idle_timeout=deps.stdio_default_idle_timeout,
        )
        if error_response is not None or updates is None:
            assert error_response is not None
            return error_response
        service = await request.app.state.repos.services.update_mcp_service(service_id, updates)
        if service is None:
            return deps.not_found("mcp_service")
        if (
            ("status" in updates and str(updates.get("status") or "") != "active")
            or ("transport_type" in updates and updates.get("transport_type") != "stdio")
        ):
            await deps.close_stdio_client_for_service(request.app, service_id)
        if (
            "endpoint_url" in updates
            or "auth_type" in updates
            or "transport_type" in updates
            or ("status" in updates and str(updates.get("status") or "") != "active")
        ):
            deps.forget_downstream_session(request.app, service_id)
        await deps.publish_list_changed(request.app, reason="service.update", resource_id=service_id)
        return JSONResponse(service)

    @router.delete("/v1/mcp-services/{service_id}")
    async def delete_service(request: Request, service_id: str) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        await request.app.state.repos.services.soft_delete_mcp_service(service_id)
        await deps.close_stdio_client_for_service(request.app, service_id)
        deps.forget_downstream_session(request.app, service_id)
        await deps.publish_list_changed(request.app, reason="service.delete", resource_id=service_id)
        return deps.accepted({"id": service_id, "status": "deleted"})

    @router.post("/v1/mcp-services/{service_id}/validate")
    async def validate_service_endpoint(request: Request, service_id: str) -> Response:
        if not deps.verify_admin_request(request):
            return deps.unauthorized_response()
        if await request.app.state.repos.services.get_mcp_service(service_id) is None:
            return deps.not_found("mcp_service")
        job = await request.app.state.repos.jobs.create_job(kind="service_validation", payload={"service_id": service_id})
        try:
            report = await deps.validate_service(request.app, service_id, job_id=job["id"], correlation_id_value=deps.correlation_id(request))
            return JSONResponse({"job_id": job["id"], **report})
        except UrlSafetyError as exc:
            return deps.api_error("unsafe_endpoint", str(exc), status_code=400, details={"job_id": job["id"]})
        except DownstreamMcpError as exc:
            return deps.api_error("validation_failed", str(exc), status_code=400, details={"job_id": job["id"]})


