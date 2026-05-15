from __future__ import annotations

from coremcp.app_factory import create_app
from coremcp.db.repository_facade import DEFAULT_TOOLBOX_ID, Repository, new_id
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
