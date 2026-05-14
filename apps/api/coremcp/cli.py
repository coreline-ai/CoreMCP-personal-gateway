from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import shutil
import sys
import tarfile
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

import httpx

MCP_PROTOCOL_VERSION = "2025-06-18"
EXPORT_FORMAT = "coremcp.cli.export"
EXPORT_VERSION = 1
SENSITIVE_RESTORE_KINDS = {"admin_token_file", "db", "fernet_key_file", "secrets_file"}
DEFAULT_CLIENT_SCOPES = ["mcp:tools.read", "mcp:tools.call"]


def _url(api_url: str, path: str) -> str:
    return f"{api_url.rstrip('/')}/{path.lstrip('/')}"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("JSON value must be an object")
    return parsed


def _parse_env_pairs(items: Sequence[str] | None) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if sep and key:
            env[key] = value
    return env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coremcp", description="CoreMCP local CLI foundation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check CoreMCP readiness and health")
    doctor.add_argument("--api-url", required=True)
    doctor.set_defaults(func=_cmd_doctor)

    service = subparsers.add_parser("service", help="manage MCP services")
    service_subparsers = service.add_subparsers(dest="service_command", required=True)

    service_add = service_subparsers.add_parser("add", help="register an MCP service")
    service_add.add_argument("--api-url", required=True)
    service_add.add_argument("--token", required=True)
    service_add.add_argument("--name", required=True)
    service_add.add_argument("--slug", required=True)
    service_add.add_argument("--endpoint-url")
    service_add.add_argument("--transport-type", choices=["http", "stdio"], default="http")
    service_add.add_argument("--stdio-command")
    service_add.add_argument("--stdio-arg", action="append", default=[])
    service_add.add_argument("--stdio-env", action="append", default=[], help="KEY=VALUE pair passed only to stdio subprocess")
    service_add.add_argument("--stdio-cwd")
    service_add.add_argument("--stdio-idle-timeout", type=int, default=300)
    service_add.set_defaults(func=_cmd_service_add)

    service_validate = service_subparsers.add_parser("validate", help="validate a registered MCP service")
    service_validate.add_argument("--api-url", required=True)
    service_validate.add_argument("--token", required=True)
    service_validate.add_argument("--service-id", required=True)
    service_validate.set_defaults(func=_cmd_service_validate)

    service_list = service_subparsers.add_parser("list", help="list registered MCP services")
    service_list.add_argument("--api-url", required=True)
    service_list.add_argument("--token", required=True)
    service_list.add_argument("--limit", type=int)
    service_list.add_argument("--status")
    service_list.set_defaults(func=_cmd_service_list)

    service_delete = service_subparsers.add_parser("delete", help="delete a registered MCP service")
    service_delete.add_argument("--api-url", required=True)
    service_delete.add_argument("--token", required=True)
    service_delete.add_argument("--service-id", required=True)
    service_delete.set_defaults(func=_cmd_service_delete)

    token = subparsers.add_parser("token", help="manage CoreMCP client tokens")
    token_subparsers = token.add_subparsers(dest="token_command", required=True)

    token_issue = token_subparsers.add_parser("issue", help="issue a client token for an external MCP client")
    token_issue.add_argument("--api-url", required=True)
    token_issue.add_argument("--token", required=True)
    token_issue.add_argument("--client-name", required=True)
    token_issue.add_argument("--scopes", nargs="+", default=None)
    token_issue.set_defaults(func=_cmd_token_issue)

    token_revoke = token_subparsers.add_parser("revoke", help="revoke a client token")
    token_revoke.add_argument("--api-url", required=True)
    token_revoke.add_argument("--token", required=True)
    token_revoke.add_argument("--token-id", required=True)
    token_revoke.set_defaults(func=_cmd_token_revoke)

    tool = subparsers.add_parser("tool", help="call MCP tools through CoreMCP")
    tool_subparsers = tool.add_subparsers(dest="tool_command", required=True)

    tool_call = tool_subparsers.add_parser("call", help="initialize MCP then call a tool")
    tool_call.add_argument("--api-url", required=True)
    tool_call.add_argument("--token", required=True)
    tool_call.add_argument("--name", required=True)
    tool_call.add_argument("--args", required=True, type=_parse_json_object)
    tool_call.set_defaults(func=_cmd_tool_call)

    export = subparsers.add_parser("export", help="create a CoreMCP local backup archive")
    export.add_argument("--to", required=True)
    export.add_argument("--db", required=True)
    export.add_argument("--secrets-file", required=True)
    export.add_argument("--fernet-key-file", required=True)
    export.add_argument("--admin-token-file", required=True)
    export.add_argument("--include-keychain", action="store_true")
    export.add_argument("--i-understand-secret-export", action="store_true")
    export.set_defaults(func=_cmd_export)

    import_ = subparsers.add_parser("import", help="inspect or restore a CoreMCP backup archive")
    import_.add_argument("--from", dest="from_path", required=True)
    import_.add_argument("--dry-run", action="store_true", help="inspect archive without writing files")
    import_.add_argument("--target-root", help="restore archive files under this directory when not using --dry-run")
    import_.add_argument("--overwrite", action="store_true", help="allow replacing existing target files during restore")
    import_.set_defaults(func=_cmd_import)

    return parser


def _response_payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"status_code": response.status_code, "text": response.text}


def _request_failed(response: httpx.Response) -> bool:
    return response.status_code >= 400


def _cmd_doctor(args: argparse.Namespace, client: httpx.Client) -> int:
    results: dict[str, Any] = {}
    ok = True
    for path in ("/ready", "/health"):
        key = path.lstrip("/")
        try:
            response = client.get(_url(args.api_url, path))
            results[key] = {"status_code": response.status_code, "body": _response_payload(response)}
            ok = ok and response.status_code == 200
        except httpx.HTTPError as exc:
            results[key] = {"error": str(exc)}
            ok = False
    _print_json({"ok": ok, "checks": results})
    return 0 if ok else 1


def _cmd_service_add(args: argparse.Namespace, client: httpx.Client) -> int:
    payload: dict[str, Any] = {
        "name": args.name,
        "slug": args.slug,
        "transport_type": args.transport_type,
    }
    if args.endpoint_url:
        payload["endpoint_url"] = args.endpoint_url
    if args.transport_type == "stdio":
        payload.update(
            {
                "stdio_command": args.stdio_command,
                "stdio_args": list(args.stdio_arg or []),
                "stdio_env": _parse_env_pairs(args.stdio_env),
                "stdio_cwd": args.stdio_cwd,
                "stdio_idle_timeout_seconds": args.stdio_idle_timeout,
            }
        )
    response = client.post(
        _url(args.api_url, "/v1/mcp-services"),
        headers=_auth_headers(args.token),
        json=payload,
    )
    _print_json(_response_payload(response))
    return 1 if _request_failed(response) else 0


def _cmd_service_validate(args: argparse.Namespace, client: httpx.Client) -> int:
    response = client.post(
        _url(args.api_url, f"/v1/mcp-services/{args.service_id}/validate"),
        headers=_auth_headers(args.token),
    )
    _print_json(_response_payload(response))
    return 1 if _request_failed(response) else 0


def _cmd_service_list(args: argparse.Namespace, client: httpx.Client) -> int:
    params: dict[str, Any] = {}
    if args.limit is not None:
        params["limit"] = args.limit
    if args.status:
        params["status"] = args.status
    response = client.get(
        _url(args.api_url, "/v1/mcp-services"),
        headers=_auth_headers(args.token),
        params=params or None,
    )
    _print_json(_response_payload(response))
    return 1 if _request_failed(response) else 0


def _cmd_service_delete(args: argparse.Namespace, client: httpx.Client) -> int:
    response = client.delete(
        _url(args.api_url, f"/v1/mcp-services/{args.service_id}"),
        headers=_auth_headers(args.token),
    )
    _print_json(_response_payload(response))
    return 1 if _request_failed(response) else 0


def _cmd_token_issue(args: argparse.Namespace, client: httpx.Client) -> int:
    scopes = list(args.scopes or DEFAULT_CLIENT_SCOPES)
    headers = _auth_headers(args.token)
    connection_response = client.post(
        _url(args.api_url, "/v1/external-connections"),
        headers=headers,
        json={
            "client_type": "codex_cli",
            "client_name": args.client_name,
            "scopes": scopes,
            "protocol_version": MCP_PROTOCOL_VERSION,
        },
    )
    connection_payload = _response_payload(connection_response)
    if _request_failed(connection_response):
        _print_json({"ok": False, "stage": "external_connection", "response": connection_payload})
        return 1
    if not isinstance(connection_payload, dict) or not isinstance(connection_payload.get("id"), str):
        _print_json({"ok": False, "stage": "external_connection", "error": "missing_connection_id", "response": connection_payload})
        return 1

    token_response = client.post(
        _url(args.api_url, "/v1/settings/client-tokens"),
        headers=headers,
        json={
            "external_connection_id": connection_payload["id"],
            "scopes": scopes,
            "protocol_version": MCP_PROTOCOL_VERSION,
        },
    )
    token_payload = _response_payload(token_response)
    output = {"ok": not _request_failed(token_response), "connection": connection_payload, "client_token": token_payload}
    _print_json(output)
    return 1 if _request_failed(token_response) else 0


def _cmd_token_revoke(args: argparse.Namespace, client: httpx.Client) -> int:
    response = client.delete(
        _url(args.api_url, f"/v1/settings/client-tokens/{args.token_id}"),
        headers=_auth_headers(args.token),
    )
    _print_json(_response_payload(response))
    return 1 if _request_failed(response) else 0


def _cmd_tool_call(args: argparse.Namespace, client: httpx.Client) -> int:
    headers = _auth_headers(args.token)
    init_response = client.post(
        _url(args.api_url, "/mcp"),
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "coremcp-cli", "version": "0.1.0"},
            },
        },
    )
    if _request_failed(init_response):
        _print_json({"initialize": _response_payload(init_response)})
        return 1

    call_headers = dict(headers)
    session_id = init_response.headers.get("Mcp-Session-Id")
    if session_id:
        call_headers["Mcp-Session-Id"] = session_id

    call_response = client.post(
        _url(args.api_url, "/mcp"),
        headers=call_headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": args.name, "arguments": args.args},
        },
    )
    _print_json(_response_payload(call_response))
    return 1 if _request_failed(call_response) else 0


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _export_file_entry(kind: str, raw_path: str, arcname: str) -> dict[str, Any]:
    path = Path(raw_path).expanduser()
    entry: dict[str, Any] = {"path": str(path), "arcname": arcname}
    if not path.exists():
        return {**entry, "status": "missing"}
    if not path.is_file():
        return {**entry, "status": "skipped", "reason": "not_file"}
    stat = path.stat()
    return {
        **entry,
        "status": "included",
        "kind": kind,
        "size_bytes": stat.st_size,
        "sha256": _sha256_file(path),
    }


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _add_bytes_to_tar(archive: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mtime = int(dt.datetime.now(dt.UTC).timestamp())
    archive.addfile(info, io.BytesIO(data))


def _cmd_export(args: argparse.Namespace, client: httpx.Client) -> int:
    if args.include_keychain and not args.i_understand_secret_export:
        _print_json(
            {
                "ok": False,
                "error": "secret_export_confirmation_required",
                "message": "Keychain export requires --include-keychain and --i-understand-secret-export.",
            }
        )
        return 1

    output_path = Path(args.to).expanduser()
    file_specs = {
        "db": (args.db, f"files/db/{Path(args.db).name or 'coremcp.sqlite3'}"),
        "secrets_file": (args.secrets_file, f"files/secrets/{Path(args.secrets_file).name or 'secrets.json'}"),
        "fernet_key_file": (args.fernet_key_file, f"files/secrets/{Path(args.fernet_key_file).name or 'fernet.key'}"),
        "admin_token_file": (args.admin_token_file, f"files/auth/{Path(args.admin_token_file).name or 'admin-token'}"),
    }
    files = {kind: _export_file_entry(kind, raw_path, arcname) for kind, (raw_path, arcname) in file_specs.items()}
    keychain = (
        {"status": "included", "note": "Keychain export placeholder only; no Keychain items are written by this skeleton."}
        if args.include_keychain
        else {"status": "excluded", "reason": "requires --include-keychain --i-understand-secret-export"}
    )
    manifest: dict[str, Any] = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "created_at": _utc_now_iso(),
        "files": files,
        "keychain": keychain,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output_path, "w") as archive:
        _add_bytes_to_tar(archive, "manifest.json", _manifest_bytes(manifest))
        for entry in files.values():
            if entry.get("status") == "included":
                archive.add(Path(str(entry["path"])), arcname=str(entry["arcname"]), recursive=False)

    _print_json({"ok": True, "archive": str(output_path), "manifest": manifest})
    return 0


def _unsafe_archive_path_reason(path: str) -> str | None:
    if not path:
        return "path is empty"
    if "\\" in path:
        return "path contains backslash"
    parsed = PurePosixPath(path)
    if parsed.is_absolute():
        return "path is absolute"
    if any(part in {"", ".", ".."} for part in parsed.parts):
        return "path contains empty, current, or parent directory segment"
    return None


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _target_path_for_arcname(target_root: Path, arcname: str) -> tuple[Path | None, str | None]:
    unsafe_reason = _unsafe_archive_path_reason(arcname)
    if unsafe_reason:
        return None, unsafe_reason
    root = target_root.expanduser().resolve(strict=False)
    target = root.joinpath(*PurePosixPath(arcname).parts)
    resolved_target = target.resolve(strict=False)
    if not _path_is_relative_to(resolved_target, root):
        return None, "target path escapes target root"
    return target, None


def _validate_archive_members(members: Sequence[tarfile.TarInfo]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for member in members:
        if member.name in seen:
            errors.append(f"archive member is duplicated: {member.name}")
        seen.add(member.name)
        unsafe_reason = _unsafe_archive_path_reason(member.name)
        if unsafe_reason:
            errors.append(f"archive member path is unsafe: {member.name} ({unsafe_reason})")
    return errors


def _validate_import_manifest(manifest: Any, members: dict[str, tarfile.TarInfo]) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]
    if manifest.get("format") != EXPORT_FORMAT:
        errors.append("manifest format is unsupported")
    if manifest.get("version") != EXPORT_VERSION:
        errors.append("manifest version is unsupported")
    files = manifest.get("files")
    if not isinstance(files, dict):
        errors.append("manifest files must be an object")
        return errors
    for kind, entry in files.items():
        if not isinstance(entry, dict):
            errors.append(f"manifest files.{kind} must be an object")
            continue
        if entry.get("status") == "included":
            arcname = entry.get("arcname")
            if not isinstance(arcname, str) or not arcname:
                errors.append(f"manifest files.{kind}.arcname is required")
            else:
                unsafe_reason = _unsafe_archive_path_reason(arcname)
                if unsafe_reason:
                    errors.append(f"manifest files.{kind}.arcname is unsafe: {arcname} ({unsafe_reason})")
                elif arcname not in members:
                    errors.append(f"archive member missing for files.{kind}: {arcname}")
                elif not members[arcname].isfile():
                    errors.append(f"archive member for files.{kind} is not a regular file: {arcname}")
    return errors


def _summarize_import_files(manifest: Any, member_names: set[str]) -> list[dict[str, Any]]:
    files = []
    if isinstance(manifest, dict) and isinstance(manifest.get("files"), dict):
        for kind, entry in manifest["files"].items():
            if isinstance(entry, dict):
                files.append(
                    {
                        "kind": kind,
                        "status": entry.get("status"),
                        "arcname": entry.get("arcname"),
                        "present": isinstance(entry.get("arcname"), str) and entry.get("arcname") in member_names,
                    }
                )
    return files


def _skipped_import_entries(manifest: Any) -> list[dict[str, Any]]:
    skipped: list[dict[str, Any]] = []
    if isinstance(manifest, dict) and isinstance(manifest.get("files"), dict):
        for kind, entry in manifest["files"].items():
            if isinstance(entry, dict) and entry.get("status") != "included":
                skipped.append({"kind": kind, "status": entry.get("status"), "reason": entry.get("reason")})

    keychain = manifest.get("keychain") if isinstance(manifest, dict) else None
    if isinstance(keychain, dict) and keychain.get("status") == "included":
        skipped.append(
            {
                "kind": "keychain",
                "status": "unsupported",
                "reason": "keychain_restore_requires_manual_recreation",
            }
        )
    return skipped


def _manifest_included_file_entries(manifest: Any) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        return []
    return [
        (str(kind), entry)
        for kind, entry in manifest["files"].items()
        if isinstance(entry, dict) and entry.get("status") == "included"
    ]


def _verify_member_sha256(archive: tarfile.TarFile, member: tarfile.TarInfo, expected_sha256: Any) -> str | None:
    if not isinstance(expected_sha256, str) or not expected_sha256:
        return None
    source = archive.extractfile(member)
    if source is None:
        return "member content could not be opened"
    digest = hashlib.sha256()
    with source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected_sha256:
        return f"sha256 mismatch: expected {expected_sha256}, got {actual}"
    return None


def _copy_member_to_target(archive: tarfile.TarFile, member: tarfile.TarInfo, target: Path) -> None:
    source = archive.extractfile(member)
    if source is None:
        raise OSError("member content could not be opened")
    target.parent.mkdir(parents=True, exist_ok=True)
    with source, target.open("wb") as destination:
        shutil.copyfileobj(source, destination)


def _cmd_import(args: argparse.Namespace, client: httpx.Client) -> int:
    del client

    archive_path = Path(args.from_path).expanduser()
    if not archive_path.is_file():
        _print_json({"ok": False, "error": "archive_not_found", "archive": str(archive_path)})
        return 1

    try:
        with tarfile.open(archive_path, "r:*") as archive:
            member_infos = archive.getmembers()
            members = [member.name for member in member_infos]
            member_map = {member.name: member for member in member_infos}
            member_validation_errors = _validate_archive_members(member_infos)
            manifest_member = member_map.get("manifest.json")
            if manifest_member is None:
                _print_json({"ok": False, "error": "manifest_missing", "archive": str(archive_path), "members": members})
                return 1
            manifest_file = archive.extractfile(manifest_member)
            if manifest_file is None:
                _print_json({"ok": False, "error": "manifest_missing", "archive": str(archive_path), "members": members})
                return 1
            try:
                with manifest_file:
                    manifest = json.loads(manifest_file.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                _print_json({"ok": False, "error": "manifest_invalid_json", "archive": str(archive_path), "message": str(exc)})
                return 1

            validation_errors = member_validation_errors + _validate_import_manifest(manifest, member_map)
            member_names = set(members)
            files = _summarize_import_files(manifest, member_names)
            skipped = _skipped_import_entries(manifest)

            if args.dry_run:
                _print_json(
                    {
                        "ok": not validation_errors,
                        "dry_run": True,
                        "archive": str(archive_path),
                        "members": members,
                        "manifest": manifest,
                        "files": files,
                        "validation_errors": validation_errors,
                        "would_overwrite": False,
                        "restored": [],
                        "skipped": skipped,
                        "errors": [{"error": "validation_failed", "message": error} for error in validation_errors],
                    }
                )
                return 1 if validation_errors else 0

            if not args.target_root:
                error = {
                    "error": "target_root_required",
                    "message": "Actual import requires --target-root. Use --dry-run to inspect without writing.",
                }
                _print_json(
                    {
                        "ok": False,
                        "dry_run": False,
                        "archive": str(archive_path),
                        "restored": [],
                        "skipped": skipped,
                        "errors": [error],
                        "validation_errors": validation_errors,
                    }
                )
                return 1

            target_root = Path(args.target_root).expanduser().resolve(strict=False)
            restore_plan: list[dict[str, Any]] = []
            restore_errors: list[dict[str, Any]] = [
                {"error": "validation_failed", "message": error} for error in validation_errors
            ]

            for kind, entry in _manifest_included_file_entries(manifest):
                arcname = str(entry["arcname"])
                target, unsafe_reason = _target_path_for_arcname(target_root, arcname)
                if target is None:
                    restore_errors.append(
                        {
                            "error": "unsafe_target_path",
                            "kind": kind,
                            "arcname": arcname,
                            "message": unsafe_reason,
                        }
                    )
                    continue

                member = member_map.get(arcname)
                if member is None:
                    continue
                checksum_error = _verify_member_sha256(archive, member, entry.get("sha256"))
                if checksum_error:
                    restore_errors.append(
                        {
                            "error": "checksum_failed",
                            "kind": kind,
                            "arcname": arcname,
                            "message": checksum_error,
                        }
                    )
                target_exists = target.exists()
                if target_exists:
                    if not args.overwrite:
                        restore_errors.append(
                            {
                                "error": "target_exists",
                                "kind": kind,
                                "arcname": arcname,
                                "target": str(target),
                                "message": "Pass --overwrite to replace this file.",
                            }
                        )
                    elif not target.is_file():
                        restore_errors.append(
                            {
                                "error": "target_not_file",
                                "kind": kind,
                                "arcname": arcname,
                                "target": str(target),
                                "message": "Refusing to overwrite a non-file target.",
                            }
                        )
                restore_plan.append(
                    {
                        "kind": kind,
                        "entry": entry,
                        "arcname": arcname,
                        "member": member,
                        "target": target,
                        "will_overwrite": target_exists,
                    }
                )

            if restore_errors:
                _print_json(
                    {
                        "ok": False,
                        "dry_run": False,
                        "archive": str(archive_path),
                        "target_root": str(target_root),
                        "restored": [],
                        "skipped": skipped,
                        "errors": restore_errors,
                        "validation_errors": validation_errors,
                    }
                )
                return 1

            restored: list[dict[str, Any]] = []
            write_errors: list[dict[str, Any]] = []
            target_root.mkdir(parents=True, exist_ok=True)
            for plan in restore_plan:
                kind = str(plan["kind"])
                target = plan["target"]
                try:
                    _copy_member_to_target(archive, plan["member"], target)
                    mode: str | None = None
                    if kind in SENSITIVE_RESTORE_KINDS:
                        target.chmod(0o600)
                        mode = "0600"
                    restored.append(
                        {
                            "kind": kind,
                            "arcname": plan["arcname"],
                            "target": str(target),
                            "overwritten": bool(plan["will_overwrite"]),
                            "mode": mode,
                        }
                    )
                except OSError as exc:
                    write_errors.append(
                        {
                            "error": "restore_failed",
                            "kind": kind,
                            "arcname": plan["arcname"],
                            "target": str(target),
                            "message": str(exc),
                        }
                    )

            _print_json(
                {
                    "ok": not write_errors,
                    "dry_run": False,
                    "archive": str(archive_path),
                    "target_root": str(target_root),
                    "restored": restored,
                    "skipped": skipped,
                    "errors": write_errors,
                    "validation_errors": [],
                }
            )
            return 1 if write_errors else 0
    except (tarfile.TarError, OSError) as exc:
        _print_json({"ok": False, "error": "archive_invalid", "archive": str(archive_path), "message": str(exc)})
        return 1


def run(argv: Sequence[str] | None = None, *, client: httpx.Client | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if client is None:
        context = httpx.Client(timeout=10.0)
    else:
        context = nullcontext(client)
    try:
        with context as active_client:
            return int(args.func(args, active_client))
    except httpx.HTTPError as exc:
        print(f"coremcp: request failed: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
