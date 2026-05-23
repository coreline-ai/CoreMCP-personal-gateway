from __future__ import annotations

import inspect
from collections import defaultdict

import pytest

from coremcp.api import (
    register_admin_meta_routes,
    register_connections_routes,
    register_mcp_routes,
    register_meta_routes,
    register_oauth_routes,
    register_playground_routes,
    register_services_routes,
    register_toolboxes_routes,
)
from coremcp.api.body_limit import RequestBodyTooLarge, install_streaming_body_limit
from coremcp.app_factory import create_app
from coremcp.auth import OAuthError, OAuthService
from coremcp.auth.admin import AdminTokenFileError
from coremcp.credentials import CredentialVaultError
from coremcp.db.repository_facade import (
    DEFAULT_TOOLBOX_ID,
    AuditRepository,
    CatalogRepository,
    ConnectionRepository,
    CredentialRepository,
    JobRepository,
    Repository,
    RepositoryFacades,
    ServiceRepository,
    ToolboxRepository,
    new_id,
)
from coremcp.db.repository_audit import AuditRepository
from coremcp.db.repository_catalog import CatalogRepositoryMixin
from coremcp.db.repository_connections import ConnectionsRepositoryMixin
from coremcp.db.repository_credentials import CredentialsRepositoryMixin
from coremcp.db.repository_jobs import JobsRepository
from coremcp.db.repository_services import ServicesRepositoryMixin
from coremcp.db.repository_toolbox import ToolboxRepositoryMixin
from coremcp.errors import CoreMcpError, CoreMcpRuntimeError, CoreMcpValueError
from coremcp.mcp import (
    PromptsHandlerDeps,
    ResourcesHandlerDeps,
    RpcHelperDeps,
    ToolsHandlerDeps,
    active_toolbox_services,
    cached_prompt_to_mcp,
    cached_resource_template_to_mcp,
    cached_resource_to_mcp,
    handle_prompts_get,
    handle_prompts_list,
    handle_resources_list,
    handle_resources_read,
    handle_tools_call,
    handle_tools_list,
    normalize_downstream_tool,
    refresh_tools,
    request_default_downstream_rpc,
    request_service_rpc,
    service_transport_type,
    toolbox_unavailable_services,
    truncate_resource_read_result,
    unambiguous_resource_rows,
)
from coremcp.proxy import CircuitOpenError, DownstreamMcpError, StdioCommandNotAllowedError, UrlSafetyError
from coremcp.settings import Settings


REPOSITORY_DOMAIN_MIXINS = (
    ServicesRepositoryMixin,
    CatalogRepositoryMixin,
    ToolboxRepositoryMixin,
    CredentialsRepositoryMixin,
    ConnectionsRepositoryMixin,
)


def _declared_public_callable_names(cls: type) -> list[str]:
    names: list[str] = []
    for name, member in cls.__dict__.items():
        if name.startswith("_"):
            continue
        if isinstance(member, (classmethod, staticmethod)):
            member = member.__func__
        if inspect.isfunction(member) or inspect.ismethod(member):
            names.append(name)
    return sorted(names)


def test_app_factory_facade_imports_create_app(tmp_path):
    app = create_app(
        Settings(
            COREMCP_ADMIN_TOKEN_VALUE="cmcp_admin_facade_testtoken",
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "facade.sqlite3",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "facade-secrets.json",
        )
    )
    assert app.title == "CoreMCP API"


def test_api_route_module_exports_are_stable():
    assert callable(register_mcp_routes)
    assert callable(register_admin_meta_routes)
    assert callable(register_meta_routes)
    assert callable(register_oauth_routes)
    assert callable(register_services_routes)
    assert callable(register_connections_routes)
    assert callable(register_toolboxes_routes)
    assert callable(install_streaming_body_limit)
    assert issubclass(RequestBodyTooLarge, Exception)
    assert callable(register_playground_routes)
    assert callable(active_toolbox_services)
    assert callable(toolbox_unavailable_services)
    assert callable(cached_resource_to_mcp)
    assert callable(cached_resource_template_to_mcp)
    assert callable(unambiguous_resource_rows)
    assert callable(truncate_resource_read_result)
    assert callable(cached_prompt_to_mcp)
    assert callable(handle_resources_list)
    assert callable(handle_resources_read)
    assert callable(handle_prompts_list)
    assert callable(handle_prompts_get)
    assert ResourcesHandlerDeps.__name__ == "ResourcesHandlerDeps"
    assert PromptsHandlerDeps.__name__ == "PromptsHandlerDeps"
    assert callable(handle_tools_list)
    assert callable(handle_tools_call)
    assert callable(refresh_tools)
    assert callable(normalize_downstream_tool)
    assert ToolsHandlerDeps.__name__ == "ToolsHandlerDeps"
    assert callable(request_service_rpc)
    assert callable(request_default_downstream_rpc)
    assert callable(service_transport_type)
    assert RpcHelperDeps.__name__ == "RpcHelperDeps"


def test_coremcp_error_base_contract_is_stable():
    value_errors = [
        OAuthError,
        StdioCommandNotAllowedError,
        UrlSafetyError,
    ]
    runtime_errors = [
        AdminTokenFileError,
        CircuitOpenError,
        CredentialVaultError,
        DownstreamMcpError,
    ]

    for exc_type in value_errors:
        assert issubclass(exc_type, CoreMcpError)
        assert issubclass(exc_type, CoreMcpValueError)
        assert issubclass(exc_type, ValueError)

    for exc_type in runtime_errors:
        assert issubclass(exc_type, CoreMcpError)
        assert issubclass(exc_type, CoreMcpRuntimeError)
        assert issubclass(exc_type, RuntimeError)


@pytest.mark.asyncio
async def test_oauth_service_shutdown_clears_in_memory_signing_material(tmp_path):
    app = create_app(
        Settings(
            COREMCP_ADMIN_TOKEN_VALUE="cmcp_admin_facade_testtoken",
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            AUTH_MODE="oauth",
            COREMCP_DB_PATH=tmp_path / "oauth-shutdown.sqlite3",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "oauth-shutdown-secrets.json",
        )
    )

    async with app.router.lifespan_context(app):
        oauth_service: OAuthService = app.state.oauth
        assert oauth_service.kid is not None
        assert oauth_service._private_key is not None
        await oauth_service.shutdown()
        assert oauth_service.kid is None
        assert oauth_service._private_key is None


@pytest.mark.asyncio
async def test_app_lifespan_exposes_repository_facades(tmp_path):
    app = create_app(
        Settings(
            COREMCP_ADMIN_TOKEN_VALUE="cmcp_admin_facade_testtoken",
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "missing-admin-token",
            COREMCP_DB_PATH=tmp_path / "facade-lifespan.sqlite3",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "facade-lifespan-secrets.json",
        )
    )

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.repos, RepositoryFacades)
        assert app.state.repos.repository is app.state.repository
        assert isinstance(app.state.repos.services, ServiceRepository)


def test_repository_facade_keeps_public_contract(tmp_path):
    repository = Repository(tmp_path / "repo.sqlite3")
    assert DEFAULT_TOOLBOX_ID == "tbx_default"
    assert new_id("test").startswith("test_")
    assert repository.database_path.name == "repo.sqlite3"


def test_repository_keeps_w1_domain_mixin_split():
    # Jobs graduated to composition (ADR-046 Step 1 / Phase 2, 2026-05-23).
    assert Repository.__bases__ == REPOSITORY_DOMAIN_MIXINS


def test_repository_jobs_is_composed_facade(tmp_path):
    repo = Repository(database_path=tmp_path / "__composition_check__.sqlite3")
    assert isinstance(repo.jobs, JobsRepository)


def test_repository_audit_is_composed_facade(tmp_path):
    repo = Repository(database_path=tmp_path / "__composition_check__.sqlite3")
    assert isinstance(repo.audit_repo, AuditRepository)


def test_repository_mro_has_no_unintended_public_callable_collisions():
    owners_by_name: dict[str, list[str]] = defaultdict(list)

    for cls in Repository.__mro__:
        if cls is object:
            continue
        for name in _declared_public_callable_names(cls):
            owners_by_name[name].append(cls.__name__)

    duplicates = {
        name: owners
        for name, owners in sorted(owners_by_name.items())
        if len(owners) > 1
    }
    assert duplicates == {}


class _RecordingRepository:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"method": name, "args": args, "kwargs": kwargs}

        return _record


def test_repository_domain_facades_are_exported():
    facade_types = [
        AuditRepository,
        CatalogRepository,
        ConnectionRepository,
        CredentialRepository,
        JobRepository,
        ServiceRepository,
        ToolboxRepository,
    ]

    for facade_type in facade_types:
        assert facade_type.__name__.endswith("Repository")


def test_repository_domain_facades_delegate_representative_methods():
    repository = _RecordingRepository()
    facades = RepositoryFacades(repository)

    representative_calls = [
        (facades.services, "list_mcp_services", (), {"limit": 3}),
        (facades.catalog, "get_catalog_tools", (), {"toolbox_id": DEFAULT_TOOLBOX_ID}),
        (facades.credentials, "get_service_credential", ("svc_1",), {}),
        (facades.connections, "list_external_connections", (), {"limit": 2}),
        (facades.toolbox, "list_toolbox_items", (DEFAULT_TOOLBOX_ID,), {}),
        (facades.audit, "recent_audit_logs", (), {"limit": 5}),
        (facades.jobs, "get_job", ("job_1",), {}),
    ]

    for facade, method_name, args, kwargs in representative_calls:
        result = getattr(facade, method_name)(*args, **kwargs)
        assert result["method"] == method_name

    assert repository.calls == [
        (method_name, args, kwargs)
        for _, method_name, args, kwargs in representative_calls
    ]
