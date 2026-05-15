from __future__ import annotations

from coremcp.app_factory import create_app
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
from coremcp.settings import Settings


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


def test_repository_facade_keeps_public_contract(tmp_path):
    repository = Repository(tmp_path / "repo.sqlite3")
    assert DEFAULT_TOOLBOX_ID == "tbx_default"
    assert new_id("test").startswith("test_")
    assert repository.database_path.name == "repo.sqlite3"


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
