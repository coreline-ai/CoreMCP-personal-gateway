from __future__ import annotations

import io
import json
import stat
import tarfile

import httpx
import pytest

from coremcp.cli import run


def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_doctor_checks_ready_and_health_without_network(capsys):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"status": "ready" if request.url.path == "/ready" else "ok"})

    code = run(["doctor", "--api-url", "http://coremcp.local"], client=make_client(handler))

    assert code == 0
    assert seen == ["/ready", "/health"]
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True


def test_doctor_returns_one_when_a_check_fails(capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        status = 503 if request.url.path == "/ready" else 200
        return httpx.Response(status, json={"status": "x"})

    code = run(["doctor", "--api-url", "http://coremcp.local/"], client=make_client(handler))

    assert code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["checks"]["ready"]["status_code"] == 503


def test_service_add_posts_expected_payload_and_bearer_token(capsys):
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("Authorization")
        captured["json"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "svc_1"})

    code = run(
        [
            "service",
            "add",
            "--api-url",
            "http://coremcp.local",
            "--token",
            "admin-token",
            "--name",
            "Fake MCP",
            "--slug",
            "fake",
            "--endpoint-url",
            "http://fake.local/mcp",
        ],
        client=make_client(handler),
    )

    assert code == 0
    assert captured == {
        "method": "POST",
        "path": "/v1/mcp-services",
        "auth": "Bearer admin-token",
        "json": {"name": "Fake MCP", "slug": "fake", "transport_type": "http", "endpoint_url": "http://fake.local/mcp"},
    }
    assert json.loads(capsys.readouterr().out) == {"id": "svc_1"}


def test_service_add_supports_stdio_payload():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "svc_stdio"})

    code = run(
        [
            "service",
            "add",
            "--api-url",
            "http://coremcp.local",
            "--token",
            "admin-token",
            "--name",
            "Local Stdio",
            "--slug",
            "local-stdio",
            "--transport-type",
            "stdio",
            "--stdio-command",
            "/usr/bin/python3",
            "--stdio-arg",
            "server.py",
            "--stdio-env",
            "SAFE=value",
        ],
        client=make_client(handler),
    )

    assert code == 0
    assert captured["json"] == {
        "name": "Local Stdio",
        "slug": "local-stdio",
        "transport_type": "stdio",
        "stdio_command": "/usr/bin/python3",
        "stdio_args": ["server.py"],
        "stdio_env": {"SAFE": "value"},
        "stdio_cwd": None,
        "stdio_idle_timeout_seconds": 300,
    }


def test_service_validate_posts_expected_endpoint():
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"status": "ok"})

    code = run(
        [
            "service",
            "validate",
            "--api-url",
            "http://coremcp.local",
            "--token",
            "admin-token",
            "--service-id",
            "svc_1",
        ],
        client=make_client(handler),
    )

    assert code == 0
    assert captured == {"path": "/v1/mcp-services/svc_1/validate", "auth": "Bearer admin-token"}


def test_tool_call_initializes_then_calls_with_session_and_json_args(capsys):
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(
            {
                "path": request.url.path,
                "auth": request.headers.get("Authorization"),
                "session": request.headers.get("Mcp-Session-Id"),
                "body": body,
            }
        )
        if body["method"] == "initialize":
            return httpx.Response(200, headers={"Mcp-Session-Id": "sess_1"}, json={"result": {}})
        return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": "ok"}]}})

    code = run(
        [
            "tool",
            "call",
            "--api-url",
            "http://coremcp.local",
            "--token",
            "client-token",
            "--name",
            "fake.echo",
            "--args",
            '{"message":"hi"}',
        ],
        client=make_client(handler),
    )

    assert code == 0
    assert [request["body"]["method"] for request in requests] == ["initialize", "tools/call"]
    assert requests[0]["session"] is None
    assert requests[1]["session"] == "sess_1"
    assert requests[1]["auth"] == "Bearer client-token"
    assert requests[1]["body"]["params"] == {"name": "fake.echo", "arguments": {"message": "hi"}}
    assert json.loads(capsys.readouterr().out)["result"]["content"][0]["text"] == "ok"


def test_tool_call_rejects_non_object_args():
    with pytest.raises(SystemExit) as exc:
        run(
            [
                "tool",
                "call",
                "--api-url",
                "http://coremcp.local",
                "--token",
                "client-token",
                "--name",
                "fake.echo",
                "--args",
                "[]",
            ],
            client=make_client(lambda request: httpx.Response(500)),
        )

    assert exc.value.code == 2


def test_token_issue_creates_external_connection_then_client_token(capsys):
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "auth": request.headers.get("Authorization"),
                "json": body,
            }
        )
        if request.url.path == "/v1/external-connections":
            return httpx.Response(201, json={"id": "ext_1", "client_name": body["client_name"], "scopes": body["scopes"]})
        return httpx.Response(
            201,
            json={
                "id": "pat_1",
                "external_connection_id": body["external_connection_id"],
                "token": "cmcp_client_secret",
                "scopes": body["scopes"],
            },
        )

    code = run(
        [
            "token",
            "issue",
            "--api-url",
            "http://coremcp.local",
            "--token",
            "admin-token",
            "--client-name",
            "Codex CLI",
            "--scopes",
            "mcp:tools.read",
            "mcp:tools.call",
        ],
        client=make_client(handler),
    )

    assert code == 0
    assert [request["path"] for request in requests] == ["/v1/external-connections", "/v1/settings/client-tokens"]
    assert {request["auth"] for request in requests} == {"Bearer admin-token"}
    assert requests[0]["json"] == {
        "client_type": "codex_cli",
        "client_name": "Codex CLI",
        "scopes": ["mcp:tools.read", "mcp:tools.call"],
        "protocol_version": "2025-06-18",
    }
    assert requests[1]["json"] == {
        "external_connection_id": "ext_1",
        "scopes": ["mcp:tools.read", "mcp:tools.call"],
        "protocol_version": "2025-06-18",
    }
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["connection"]["id"] == "ext_1"
    assert output["client_token"]["token"] == "cmcp_client_secret"


def test_token_revoke_deletes_client_token(capsys):
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(202, json={"id": "pat_1", "status": "revoked"})

    code = run(
        [
            "token",
            "revoke",
            "--api-url",
            "http://coremcp.local",
            "--token",
            "admin-token",
            "--token-id",
            "pat_1",
        ],
        client=make_client(handler),
    )

    assert code == 0
    assert captured == {
        "method": "DELETE",
        "path": "/v1/settings/client-tokens/pat_1",
        "auth": "Bearer admin-token",
    }
    assert json.loads(capsys.readouterr().out) == {"id": "pat_1", "status": "revoked"}


def test_service_list_gets_registered_services(capsys):
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["query"] = request.url.query.decode("utf-8")
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"items": [{"id": "svc_1", "name": "Fake MCP"}], "next_cursor": None})

    code = run(
        [
            "service",
            "list",
            "--api-url",
            "http://coremcp.local",
            "--token",
            "admin-token",
            "--limit",
            "25",
            "--status",
            "active",
        ],
        client=make_client(handler),
    )

    assert code == 0
    assert captured == {
        "method": "GET",
        "path": "/v1/mcp-services",
        "query": "limit=25&status=active",
        "auth": "Bearer admin-token",
    }
    assert json.loads(capsys.readouterr().out)["items"][0]["id"] == "svc_1"


def test_service_delete_deletes_registered_service(capsys):
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(202, json={"id": "svc_1", "status": "deleted"})

    code = run(
        [
            "service",
            "delete",
            "--api-url",
            "http://coremcp.local",
            "--token",
            "admin-token",
            "--service-id",
            "svc_1",
        ],
        client=make_client(handler),
    )

    assert code == 0
    assert captured == {"method": "DELETE", "path": "/v1/mcp-services/svc_1", "auth": "Bearer admin-token"}
    assert json.loads(capsys.readouterr().out) == {"id": "svc_1", "status": "deleted"}


def test_export_writes_manifest_and_only_existing_files(tmp_path, capsys):
    db = tmp_path / "coremcp.sqlite3"
    fernet_key = tmp_path / "fernet.key"
    admin_token = tmp_path / "admin-token"
    missing_secrets = tmp_path / "missing-secrets.json"
    archive = tmp_path / "coremcp-backup.tar"
    db.write_text("sqlite", encoding="utf-8")
    fernet_key.write_text("fernet", encoding="utf-8")
    admin_token.write_text("admin", encoding="utf-8")

    code = run(
        [
            "export",
            "--to",
            str(archive),
            "--db",
            str(db),
            "--secrets-file",
            str(missing_secrets),
            "--fernet-key-file",
            str(fernet_key),
            "--admin-token-file",
            str(admin_token),
        ]
    )

    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["manifest"]["keychain"]["status"] == "excluded"
    assert output["manifest"]["files"]["secrets_file"]["status"] == "missing"

    with tarfile.open(archive, "r:*") as tar:
        members = tar.getnames()
        manifest = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))  # type: ignore[union-attr]

    assert "manifest.json" in members
    assert manifest["format"] == "coremcp.cli.export"
    assert manifest["files"]["db"]["status"] == "included"
    assert manifest["files"]["secrets_file"]["arcname"] not in members
    assert manifest["files"]["db"]["arcname"] in members
    assert manifest["files"]["fernet_key_file"]["arcname"] in members
    assert manifest["files"]["admin_token_file"]["arcname"] in members


def test_import_dry_run_validates_manifest_and_lists_files(tmp_path, capsys):
    db = tmp_path / "coremcp.sqlite3"
    secrets = tmp_path / "secrets.json"
    fernet_key = tmp_path / "fernet.key"
    admin_token = tmp_path / "admin-token"
    archive = tmp_path / "coremcp-backup.tar"
    for path in (db, secrets, fernet_key, admin_token):
        path.write_text(path.name, encoding="utf-8")

    export_code = run(
        [
            "export",
            "--to",
            str(archive),
            "--db",
            str(db),
            "--secrets-file",
            str(secrets),
            "--fernet-key-file",
            str(fernet_key),
            "--admin-token-file",
            str(admin_token),
        ]
    )
    assert export_code == 0
    capsys.readouterr()

    import_code = run(["import", "--from", str(archive), "--dry-run"])

    assert import_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["dry_run"] is True
    assert output["would_overwrite"] is False
    assert output["validation_errors"] == []
    assert "manifest.json" in output["members"]
    assert {item["kind"] for item in output["files"]} == {"db", "secrets_file", "fernet_key_file", "admin_token_file"}
    assert all(item["present"] for item in output["files"])


def test_import_actual_restore_to_target_root_preserves_arcname_paths(tmp_path, capsys):
    db = tmp_path / "coremcp.sqlite3"
    secrets = tmp_path / "secrets.json"
    fernet_key = tmp_path / "fernet.key"
    admin_token = tmp_path / "admin-token"
    archive = tmp_path / "coremcp-backup.tar"
    target_root = tmp_path / "restore-target"
    for path in (db, secrets, fernet_key, admin_token):
        path.write_text(path.name, encoding="utf-8")

    export_code = run(
        [
            "export",
            "--to",
            str(archive),
            "--db",
            str(db),
            "--secrets-file",
            str(secrets),
            "--fernet-key-file",
            str(fernet_key),
            "--admin-token-file",
            str(admin_token),
        ]
    )
    assert export_code == 0
    capsys.readouterr()

    import_code = run(["import", "--from", str(archive), "--target-root", str(target_root)])

    assert import_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["dry_run"] is False
    assert output["errors"] == []
    assert {item["kind"] for item in output["restored"]} == {"db", "secrets_file", "fernet_key_file", "admin_token_file"}
    assert (target_root / "files/db/coremcp.sqlite3").read_text(encoding="utf-8") == "coremcp.sqlite3"
    assert (target_root / "files/secrets/secrets.json").read_text(encoding="utf-8") == "secrets.json"
    assert (target_root / "files/secrets/fernet.key").read_text(encoding="utf-8") == "fernet.key"
    assert (target_root / "files/auth/admin-token").read_text(encoding="utf-8") == "admin-token"
    assert stat.S_IMODE((target_root / "files/auth/admin-token").stat().st_mode) == 0o600
    assert stat.S_IMODE((target_root / "files/secrets/secrets.json").stat().st_mode) == 0o600


def test_import_actual_restore_refuses_existing_file_without_overwrite(tmp_path, capsys):
    db = tmp_path / "coremcp.sqlite3"
    secrets = tmp_path / "secrets.json"
    fernet_key = tmp_path / "fernet.key"
    admin_token = tmp_path / "admin-token"
    archive = tmp_path / "coremcp-backup.tar"
    target_root = tmp_path / "restore-target"
    existing_db = target_root / "files/db/coremcp.sqlite3"
    for path in (db, secrets, fernet_key, admin_token):
        path.write_text(path.name, encoding="utf-8")
    existing_db.parent.mkdir(parents=True)
    existing_db.write_text("keep-me", encoding="utf-8")

    export_code = run(
        [
            "export",
            "--to",
            str(archive),
            "--db",
            str(db),
            "--secrets-file",
            str(secrets),
            "--fernet-key-file",
            str(fernet_key),
            "--admin-token-file",
            str(admin_token),
        ]
    )
    assert export_code == 0
    capsys.readouterr()

    import_code = run(["import", "--from", str(archive), "--target-root", str(target_root)])

    assert import_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["restored"] == []
    assert output["errors"][0]["error"] == "target_exists"
    assert existing_db.read_text(encoding="utf-8") == "keep-me"


def test_import_actual_restore_allows_existing_file_with_overwrite(tmp_path, capsys):
    db = tmp_path / "coremcp.sqlite3"
    secrets = tmp_path / "secrets.json"
    fernet_key = tmp_path / "fernet.key"
    admin_token = tmp_path / "admin-token"
    archive = tmp_path / "coremcp-backup.tar"
    target_root = tmp_path / "restore-target"
    existing_db = target_root / "files/db/coremcp.sqlite3"
    for path in (db, secrets, fernet_key, admin_token):
        path.write_text(path.name, encoding="utf-8")
    existing_db.parent.mkdir(parents=True)
    existing_db.write_text("replace-me", encoding="utf-8")

    export_code = run(
        [
            "export",
            "--to",
            str(archive),
            "--db",
            str(db),
            "--secrets-file",
            str(secrets),
            "--fernet-key-file",
            str(fernet_key),
            "--admin-token-file",
            str(admin_token),
        ]
    )
    assert export_code == 0
    capsys.readouterr()

    import_code = run(["import", "--from", str(archive), "--target-root", str(target_root), "--overwrite"])

    assert import_code == 0
    assert (target_root / "files/db/coremcp.sqlite3").read_text(encoding="utf-8") == "coremcp.sqlite3"


def test_import_rejects_path_traversal_archive(tmp_path, capsys):
    archive = tmp_path / "malicious.tar"
    manifest = {
        "format": "coremcp.cli.export",
        "version": 1,
        "created_at": "2026-05-14T00:00:00Z",
        "files": {
            "db": {
                "status": "included",
                "kind": "db",
                "arcname": "../escape.sqlite3",
                "size_bytes": 6,
            }
        },
        "keychain": {"status": "excluded"},
    }
    with tarfile.open(archive, "w") as tar:
        manifest_data = json.dumps(manifest).encode("utf-8")
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest_data)
        tar.addfile(manifest_info, io.BytesIO(manifest_data))
        data = b"sqlite"
        evil_info = tarfile.TarInfo("../escape.sqlite3")
        evil_info.size = len(data)
        tar.addfile(evil_info, io.BytesIO(data))

    import_code = run(["import", "--from", str(archive), "--target-root", str(tmp_path / "restore-target")])

    assert import_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert any("unsafe" in error["message"] for error in output["errors"] if error["error"] == "validation_failed")
    assert not (tmp_path / "escape.sqlite3").exists()


def test_keychain_export_requires_explicit_secret_confirmation(tmp_path, capsys):
    archive = tmp_path / "coremcp-backup.tar"

    code = run(
        [
            "export",
            "--to",
            str(archive),
            "--db",
            str(tmp_path / "db.sqlite3"),
            "--secrets-file",
            str(tmp_path / "secrets.json"),
            "--fernet-key-file",
            str(tmp_path / "fernet.key"),
            "--admin-token-file",
            str(tmp_path / "admin-token"),
            "--include-keychain",
        ]
    )

    assert code == 1
    assert not archive.exists()
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "secret_export_confirmation_required"
