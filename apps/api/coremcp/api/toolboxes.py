from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from coremcp.api.dependencies import get_repos
from coremcp.db.repository_facade import RepositoryFacades


def register_toolboxes_routes(
    app: FastAPI,
    *,
    verify_admin_request: Callable[[Request], bool],
    unauthorized_response: Callable[..., JSONResponse],
    json_body: Callable[[Request], Awaitable[dict[str, Any] | JSONResponse]],
    api_error: Callable[..., JSONResponse],
    not_found: Callable[[str], JSONResponse],
    accepted: Callable[[dict[str, Any]], JSONResponse],
    publish_list_changed: Callable[..., Awaitable[None]],
) -> None:
    @app.get("/v1/toolboxes")
    async def list_toolboxes(
        request: Request,
        limit: int = 50,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        items = await repos.toolbox.list_toolboxes(limit=max(1, min(limit, 100)))
        return JSONResponse({"items": items, "next_cursor": None})

    @app.get("/v1/toolboxes/{toolbox_id}")
    async def get_toolbox(
        request: Request,
        toolbox_id: str,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        toolboxes = await repos.toolbox.list_toolboxes(limit=100)
        toolbox = next((item for item in toolboxes if item["id"] == toolbox_id), None)
        if toolbox is None:
            return not_found("toolbox")
        items = await repos.toolbox.list_toolbox_items(toolbox_id)
        return JSONResponse({**toolbox, "items": items})

    @app.post("/v1/toolboxes/{toolbox_id}/items")
    async def add_toolbox_item(
        request: Request,
        toolbox_id: str,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await json_body(request)
        if isinstance(body, JSONResponse):
            return body
        service_id = body.get("service_id")
        if not isinstance(service_id, str):
            return api_error("validation_failed", "service_id is required", status_code=422)
        if await repos.services.get_mcp_service(service_id) is None:
            return not_found("mcp_service")
        item = await repos.toolbox.add_toolbox_item(
            toolbox_id, service_id, enabled=bool(body.get("enabled", True))
        )
        await publish_list_changed(
            request.app,
            reason="toolbox_item.upsert",
            resource_id=item.get("id"),
            categories=("tools",),
        )
        return JSONResponse(item, status_code=201)

    @app.patch("/v1/toolboxes/{toolbox_id}/items/{item_id}")
    async def patch_toolbox_item(
        request: Request,
        toolbox_id: str,
        item_id: str,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        body = await json_body(request)
        if isinstance(body, JSONResponse):
            return body
        item = await repos.toolbox.update_toolbox_item(item_id, enabled=bool(body.get("enabled", True)))
        if item is None or item["toolbox_id"] != toolbox_id:
            return not_found("toolbox_item")
        await publish_list_changed(
            request.app,
            reason="toolbox_item.update",
            resource_id=item_id,
            categories=("tools",),
        )
        return JSONResponse(item)

    @app.delete("/v1/toolboxes/{toolbox_id}/items/{item_id}")
    async def delete_toolbox_item(
        request: Request,
        toolbox_id: str,
        item_id: str,
        repos: RepositoryFacades = Depends(get_repos),
    ) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        await repos.toolbox.delete_toolbox_item(item_id)
        await publish_list_changed(
            request.app,
            reason="toolbox_item.delete",
            resource_id=item_id,
            categories=("tools",),
        )
        return accepted({"id": item_id, "status": "deleted"})
