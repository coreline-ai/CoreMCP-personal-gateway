from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from coremcp.api._schemas import (
    AuditLogList,
    DashboardSummary,
    MeResponse,
    SettingsResponse,
    ToolInvocationList,
)
from coremcp.api.dependencies import get_repos, get_settings
from coremcp.auth.admin import AdminTokenFileError, generate_admin_token, write_admin_token_atomic
from coremcp.credentials import mask_secret
from coremcp.db.repository_facade import RepositoryFacades
from coremcp.settings import Settings


def register_admin_meta_routes(
    app: FastAPI,
    *,
    verify_admin_request: Callable[[Request], bool],
    unauthorized_response: Callable[..., JSONResponse],
    api_error: Callable[..., JSONResponse],
    not_found: Callable[[str], JSONResponse],
    request_ip: Callable[[Request], str | None],
    correlation_id: Callable[[Request], str],
) -> None:
    @app.get("/v1/me", response_model=MeResponse)
    async def me(request: Request, repos: RepositoryFacades = Depends(get_repos)) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        return JSONResponse(await repos.repository.get_me())

    @app.get("/v1/settings", response_model=SettingsResponse)
    async def settings_endpoint(
        request: Request,
        settings_obj: Settings = Depends(get_settings),
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        token_count = await repos.audit.count_active_client_tokens()
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

    @app.get("/v1/dashboard/summary", response_model=DashboardSummary)
    async def dashboard_summary(request: Request, repos: RepositoryFacades = Depends(get_repos)) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        return JSONResponse(await repos.audit.dashboard_summary())

    @app.post("/v1/settings/admin-token/rotate")
    async def rotate_admin_token(
        request: Request,
        settings_obj: Settings = Depends(get_settings),
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
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
        await repos.audit.log_audit(
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

    @app.get("/v1/tool-invocations", response_model=ToolInvocationList)
    async def list_tool_invocations(
        request: Request,
        limit: int = 20,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        items = await repos.audit.recent_invocations(limit=max(1, min(limit, 100)))
        return JSONResponse({"items": items, "next_cursor": None})

    @app.get("/v1/audit-logs", response_model=AuditLogList)
    async def list_audit_logs(
        request: Request,
        limit: int = 20,
        action: str | None = None,
        resource_type: str | None = None,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        items = await repos.audit.recent_audit_logs(
            limit=max(1, min(limit, 100)),
            action=action,
            resource_type=resource_type,
        )
        return JSONResponse({"items": items, "next_cursor": None})

    @app.get("/v1/jobs/{job_id}")
    async def get_job(
        request: Request,
        job_id: str,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        job = await repos.jobs.get_job(job_id)
        if job is None:
            return not_found("job")
        return JSONResponse(job)
