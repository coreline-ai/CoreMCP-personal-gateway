from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from coremcp.db.migrations import run_migrations

LOCAL_USER_ID = "usr_local"
DEFAULT_TOOLBOX_ID = "tbx_default"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Repository:
    """SQLite repository for the personal CoreMCP gateway.

    The repository intentionally keeps a small aiosqlite surface for the P1
    single-process runtime while Alembic owns the same schema for explicit CLI
    migrations. Startup bootstrap is idempotent so tests and local dev do not
    need to invoke Alembic manually.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if str(self.database_path) == ":memory:":
            raise RuntimeError("Repository database_path=':memory:' is not supported; use a file-backed SQLite path")

        database_path = self.database_path.expanduser().resolve()
        await asyncio.to_thread(run_migrations, database_path)

        self._db = await aiosqlite.connect(str(database_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self.bootstrap()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Repository is not connected")
        return self._db

    async def healthcheck(self) -> bool:
        cursor = await self.db.execute("SELECT 1 AS ok")
        row = await cursor.fetchone()
        return bool(row and row["ok"] == 1)

    async def bootstrap(self) -> None:
        await self.db.execute(
            """
            INSERT OR IGNORE INTO users (id, email, name, bootstrap_completed_at)
            VALUES (?, 'me@local', 'Personal', CURRENT_TIMESTAMP)
            """,
            (LOCAL_USER_ID,),
        )
        await self.db.execute(
            """
            INSERT OR IGNORE INTO toolboxes (id, owner_user_id, name, slug, is_default)
            VALUES (?, ?, 'Default', 'default', 1)
            """,
            (DEFAULT_TOOLBOX_ID, LOCAL_USER_ID),
        )
        await self.db.commit()

    # ------------------------------------------------------------------
    # JSON helpers
    # ------------------------------------------------------------------
    @staticmethod
    def dumps_json(value: Any) -> str:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def dumps_json_array(value: Any) -> str:
        return json.dumps(value if value is not None else [], ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def loads_json(value: Any, default: Any = None) -> Any:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(str(value))
        except json.JSONDecodeError:
            return default

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row | None, json_fields: Iterable[str] = ()) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        for field in json_fields:
            if field in item:
                fallback: Any = [] if field.endswith("s") or field.endswith("_json") else {}
                item[field] = Repository.loads_json(item[field], fallback)
        return item

    # ------------------------------------------------------------------
    # User / settings
    # ------------------------------------------------------------------
    async def get_me(self) -> dict[str, Any]:
        cursor = await self.db.execute(
            """
            SELECT u.id, u.email, u.name, u.locale, u.bootstrap_completed_at, u.created_at,
                   t.id AS default_toolbox_id
            FROM users u
            LEFT JOIN toolboxes t ON t.owner_user_id = u.id AND t.is_default = 1 AND t.deleted_at IS NULL
            WHERE u.id = ?
            """,
            (LOCAL_USER_ID,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("local user bootstrap row is missing")
        return dict(row)

    async def count_active_client_tokens(self) -> int:
        cursor = await self.db.execute(
            "SELECT COUNT(*) AS count FROM personal_access_tokens WHERE status = 'active' AND revoked_at IS NULL"
        )
        row = await cursor.fetchone()
        return int(row["count"])

    # ------------------------------------------------------------------
    # Audit / invocation logs
    # ------------------------------------------------------------------
    async def log_audit(
        self,
        *,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        audit_id = new_id("aud")
        await self.db.execute(
            """
            INSERT INTO audit_logs
              (id, request_id, actor_user_id, action, resource_type, resource_id, ip, user_agent, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                request_id,
                LOCAL_USER_ID,
                action,
                resource_type,
                resource_id,
                ip,
                user_agent,
                self.dumps_json(metadata or {}),
            ),
        )
        await self.db.commit()
        return audit_id

    async def log_invocation(
        self,
        *,
        session_id: str | None,
        method: str,
        tool_name: str | None,
        status: str,
        error_code: int | str | None = None,
        latency_ms: int | None = None,
        request_id: str | None = None,
        external_connection_id: str | None = None,
        service_id: str | None = None,
        service_tool_id: str | None = None,
        downstream_tool_name: str | None = None,
        downstream_latency_ms: int | None = None,
        error_message: str | None = None,
        protocol_version: str | None = None,
        idempotency_key: str | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        invocation_id = new_id("inv")
        exposed_tool_name = tool_name or method
        await self.db.execute(
            """
            INSERT INTO tool_invocations
              (id, request_id, user_id, external_connection_id, toolbox_id, service_id, service_tool_id,
               session_id, method, tool_name, exposed_tool_name, downstream_tool_name, status, error_code,
               error_message, latency_ms, downstream_latency_ms, protocol_version, idempotency_key,
               client_ip, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invocation_id,
                request_id or invocation_id,
                LOCAL_USER_ID,
                external_connection_id,
                DEFAULT_TOOLBOX_ID,
                service_id,
                service_tool_id,
                session_id,
                method,
                tool_name,
                exposed_tool_name,
                downstream_tool_name,
                status,
                None if error_code is None else str(error_code),
                error_message,
                latency_ms,
                downstream_latency_ms,
                protocol_version,
                idempotency_key,
                client_ip,
                user_agent,
            ),
        )
        await self.db.commit()
        return invocation_id

    async def count_invocations(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) AS count FROM tool_invocations")
        row = await cursor.fetchone()
        return int(row["count"])

    async def metrics_snapshot(self) -> dict[str, int]:
        queries = {
            "mcp_services_total": "SELECT COUNT(*) AS count FROM mcp_services WHERE deleted_at IS NULL",
            "mcp_services_active": "SELECT COUNT(*) AS count FROM mcp_services WHERE deleted_at IS NULL AND status = 'active'",
            "mcp_services_health_failing": "SELECT COUNT(*) AS count FROM mcp_services WHERE deleted_at IS NULL AND consecutive_failures > 0",
            "mcp_services_circuit_open": "SELECT COUNT(*) AS count FROM mcp_services WHERE deleted_at IS NULL AND circuit_open_until IS NOT NULL AND circuit_open_until > CURRENT_TIMESTAMP",
            "external_connections_active": "SELECT COUNT(*) AS count FROM external_connections WHERE status = 'active'",
            "personal_access_tokens_active": "SELECT COUNT(*) AS count FROM personal_access_tokens WHERE status = 'active' AND revoked_at IS NULL",
            "mcp_requests_total": "SELECT COUNT(*) AS count FROM tool_invocations",
            "tool_calls_total": "SELECT COUNT(*) AS count FROM tool_invocations WHERE method = 'tools/call'",
            "tool_call_errors_total": "SELECT COUNT(*) AS count FROM tool_invocations WHERE method = 'tools/call' AND status != 'success'",
            "auth_failures_total": "SELECT COUNT(*) AS count FROM audit_logs WHERE action = 'auth.failure'",
            "policy_denials_total": "SELECT COUNT(*) AS count FROM tool_invocations WHERE status = 'policy_denied'",
            "downstream_timeouts_total": "SELECT COUNT(*) AS count FROM tool_invocations WHERE status = 'timeout' OR error_code = 'downstream_timeout'",
            "tool_invocations_total": "SELECT COUNT(*) AS count FROM tool_invocations",
            "audit_logs_total": "SELECT COUNT(*) AS count FROM audit_logs",
        }
        snapshot: dict[str, int] = {}
        for key, query in queries.items():
            cursor = await self.db.execute(query)
            row = await cursor.fetchone()
            snapshot[key] = int(row["count"])
        return snapshot

    async def dashboard_summary(self) -> dict[str, Any]:
        metrics = await self.metrics_snapshot()

        cursor = await self.db.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM mcp_services
            WHERE deleted_at IS NULL
            GROUP BY status
            ORDER BY status
            """
        )
        service_status_counts = {str(row["status"]): int(row["count"]) for row in await cursor.fetchall()}

        cursor = await self.db.execute(
            """
            SELECT
              COUNT(*) AS calls,
              SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS errors,
              COALESCE(AVG(latency_ms), 0) AS avg_latency_ms,
              COALESCE(MAX(latency_ms), 0) AS max_latency_ms
            FROM tool_invocations
            WHERE method = 'tools/call' AND created_at >= datetime('now', '-24 hours')
            """
        )
        row = await cursor.fetchone()
        calls_24h = {
            "calls": int(row["calls"] or 0),
            "errors": int(row["errors"] or 0),
            "avg_latency_ms": int(float(row["avg_latency_ms"] or 0)),
            "max_latency_ms": int(row["max_latency_ms"] or 0),
        }

        cursor = await self.db.execute(
            """
            SELECT COALESCE(exposed_tool_name, tool_name, downstream_tool_name, 'unknown') AS tool,
                   COUNT(*) AS calls,
                   SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS errors,
                   COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
            FROM tool_invocations
            WHERE method = 'tools/call' AND created_at >= datetime('now', '-24 hours')
            GROUP BY tool
            ORDER BY calls DESC, tool ASC
            LIMIT 5
            """
        )
        top_tools = [
            {
                "tool": str(item["tool"]),
                "calls": int(item["calls"]),
                "errors": int(item["errors"] or 0),
                "avg_latency_ms": int(float(item["avg_latency_ms"] or 0)),
            }
            for item in await cursor.fetchall()
        ]

        cursor = await self.db.execute(
            """
            SELECT id, name, slug, status, consecutive_failures, last_health_check_at, circuit_open_until
            FROM mcp_services
            WHERE deleted_at IS NULL
              AND (consecutive_failures > 0 OR (circuit_open_until IS NOT NULL AND circuit_open_until > CURRENT_TIMESTAMP))
            ORDER BY consecutive_failures DESC, updated_at DESC
            LIMIT 5
            """
        )
        unhealthy_services = [dict(item) for item in await cursor.fetchall()]

        return {
            "metrics": metrics,
            "service_status_counts": service_status_counts,
            "calls_24h": calls_24h,
            "top_tools_24h": top_tools,
            "unhealthy_services": unhealthy_services,
        }

    async def recent_invocations(self, limit: int = 20) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            """
            SELECT id, request_id, method, tool_name, exposed_tool_name, status, error_code,
                   error_message, latency_ms, service_id, service_tool_id, downstream_tool_name,
                   protocol_version, client_ip, user_agent, created_at
            FROM tool_invocations
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def recent_audit_logs(
        self,
        limit: int = 20,
        *,
        action: str | None = None,
        resource_type: str | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if action:
            where.append("action = ?")
            params.append(action)
        if resource_type:
            where.append("resource_type = ?")
            params.append(resource_type)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(limit)
        cursor = await self.db.execute(
            f"""
            SELECT id, request_id, action, resource_type, resource_id, ip, user_agent, metadata, created_at
            FROM audit_logs
            {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["metadata"] = self.loads_json(item.get("metadata"), {})
            items.append(item)
        return items

    # ------------------------------------------------------------------
    # External connections / client tokens
    # ------------------------------------------------------------------
    async def create_external_connection(
        self,
        *,
        client_type: str,
        client_name: str | None,
        toolbox_id: str | None = None,
        oauth_client_id: str | None = None,
        protocol_version: str | None = None,
        scopes: list[str] | None = None,
        created_ip: str | None = None,
        created_user_agent: str | None = None,
    ) -> dict[str, Any]:
        connection_id = new_id("ext")
        await self.db.execute(
            """
            INSERT INTO external_connections
              (id, user_id, toolbox_id, client_type, client_name, oauth_client_id, protocol_version, scopes, created_ip, created_user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                connection_id,
                LOCAL_USER_ID,
                toolbox_id or DEFAULT_TOOLBOX_ID,
                client_type,
                client_name,
                oauth_client_id,
                protocol_version,
                self.dumps_json_array(scopes or []),
                created_ip,
                created_user_agent,
            ),
        )
        await self.log_audit(action="external_connection.create", resource_type="external_connection", resource_id=connection_id)
        return await self.get_external_connection(connection_id) or {}

    async def get_external_connection(self, connection_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            SELECT id, toolbox_id, client_type, client_name, oauth_client_id, protocol_version, status, scopes,
                   last_used_at, revoked_at, created_at, updated_at
            FROM external_connections
            WHERE id = ?
            """,
            (connection_id,),
        )
        return self._row_to_dict(await cursor.fetchone(), json_fields=("scopes",))

    async def list_external_connections(self, limit: int = 50) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            """
            SELECT id, toolbox_id, client_type, client_name, oauth_client_id, protocol_version, status, scopes,
                   last_used_at, revoked_at, created_at, updated_at
            FROM external_connections
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [self._row_to_dict(row, json_fields=("scopes",)) or {} for row in await cursor.fetchall()]

    async def revoke_external_connection(self, connection_id: str) -> bool:
        cursor = await self.db.execute(
            """
            UPDATE external_connections
            SET status = 'revoked', revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status != 'revoked'
            """,
            (connection_id,),
        )
        await self.db.execute(
            """
            UPDATE personal_access_tokens
            SET status = 'revoked', revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
            WHERE external_connection_id = ? AND status = 'active'
            """,
            (connection_id,),
        )
        await self.log_audit(
            action="external_connection.revoke", resource_type="external_connection", resource_id=connection_id
        )
        return cursor.rowcount > 0

    async def create_personal_access_token(
        self,
        *,
        external_connection_id: str,
        token_hash: str,
        token_prefix: str,
        scopes: list[str] | None = None,
        protocol_version: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        token_id = new_id("pat")
        connection = await self.get_external_connection(external_connection_id)
        if connection is None or connection["status"] != "active":
            raise ValueError("active external_connection_id is required")
        await self.db.execute(
            """
            INSERT INTO personal_access_tokens
              (id, external_connection_id, user_id, token_hash, token_prefix, scopes, protocol_version, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token_id,
                external_connection_id,
                LOCAL_USER_ID,
                token_hash,
                token_prefix,
                self.dumps_json_array(scopes or ["mcp:tools.read", "mcp:tools.call"]),
                protocol_version,
                expires_at,
            ),
        )
        await self.log_audit(action="client_token.issue", resource_type="personal_access_token", resource_id=token_id)
        return await self.get_personal_access_token(token_id) or {}

    async def get_personal_access_token(self, token_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            SELECT id, external_connection_id, token_prefix, scopes, protocol_version, status,
                   last_used_at, expires_at, revoked_at, created_at
            FROM personal_access_tokens
            WHERE id = ?
            """,
            (token_id,),
        )
        return self._row_to_dict(await cursor.fetchone(), json_fields=("scopes",))

    async def list_personal_access_tokens(self, limit: int = 50) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            """
            SELECT id, external_connection_id, token_prefix, scopes, protocol_version, status,
                   last_used_at, expires_at, revoked_at, created_at
            FROM personal_access_tokens
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [self._row_to_dict(row, json_fields=("scopes",)) or {} for row in await cursor.fetchall()]

    async def find_active_personal_access_token_by_hash(self, token_hash: str) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            SELECT pat.id, pat.external_connection_id, pat.user_id, pat.token_prefix, pat.scopes,
                   pat.protocol_version, pat.status, pat.expires_at, ec.status AS external_connection_status
            FROM personal_access_tokens pat
            JOIN external_connections ec ON ec.id = pat.external_connection_id
            WHERE pat.token_hash = ?
              AND pat.status = 'active'
              AND pat.revoked_at IS NULL
              AND (pat.expires_at IS NULL OR pat.expires_at > CURRENT_TIMESTAMP)
              AND ec.status = 'active'
            """,
            (token_hash,),
        )
        item = self._row_to_dict(await cursor.fetchone(), json_fields=("scopes",))
        if item is None:
            return None
        await self.db.execute("UPDATE personal_access_tokens SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?", (item["id"],))
        await self.db.execute(
            "UPDATE external_connections SET last_used_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (item["external_connection_id"],),
        )
        await self.db.commit()
        return item

    async def revoke_personal_access_token(self, token_id: str) -> bool:
        cursor = await self.db.execute(
            """
            UPDATE personal_access_tokens
            SET status = 'revoked', revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
            WHERE id = ? AND status = 'active'
            """,
            (token_id,),
        )
        await self.log_audit(action="client_token.revoke", resource_type="personal_access_token", resource_id=token_id)
        return cursor.rowcount > 0

    async def create_connection_token(
        self,
        *,
        token_hash: str,
        client_type: str,
        toolbox_id: str | None,
        requested_scopes: list[str],
        expires_at: str,
        created_ip: str | None = None,
        created_user_agent: str | None = None,
    ) -> dict[str, Any]:
        token_id = new_id("otk")
        await self.db.execute(
            """
            INSERT INTO connection_tokens
              (id, user_id, toolbox_id, token_hash, client_type, requested_scopes, created_ip, created_user_agent, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token_id,
                LOCAL_USER_ID,
                toolbox_id or DEFAULT_TOOLBOX_ID,
                token_hash,
                client_type,
                self.dumps_json_array(requested_scopes),
                created_ip,
                created_user_agent,
                expires_at,
            ),
        )
        await self.log_audit(action="connection_token.issue", resource_type="connection_token", resource_id=token_id)
        return await self.get_connection_token(token_id) or {}

    async def get_connection_token(self, token_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            SELECT id, toolbox_id, client_type, requested_scopes, expires_at, used_at, revoked_at, created_at
            FROM connection_tokens
            WHERE id = ?
            """,
            (token_id,),
        )
        return self._row_to_dict(await cursor.fetchone(), json_fields=("requested_scopes",))

    async def consume_connection_token(
        self,
        *,
        token_hash: str,
        used_ip: str | None = None,
        used_user_agent: str | None = None,
    ) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            UPDATE connection_tokens
            SET used_at = CURRENT_TIMESTAMP, used_ip = ?, used_user_agent = ?
            WHERE token_hash = ?
              AND used_at IS NULL
              AND revoked_at IS NULL
              AND expires_at > CURRENT_TIMESTAMP
            RETURNING id, toolbox_id, client_type, requested_scopes, expires_at, used_at
            """,
            (used_ip, used_user_agent, token_hash),
        )
        item = self._row_to_dict(await cursor.fetchone(), json_fields=("requested_scopes",))
        if item is None:
            return None
        await self.log_audit(action="connection_token.exchange", resource_type="connection_token", resource_id=item["id"])
        return item

    # ------------------------------------------------------------------
    # OAuth persistence
    # ------------------------------------------------------------------
    async def get_active_oauth_signing_key(self) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            SELECT kid, private_key_pem, alg, status, created_at
            FROM oauth_signing_keys
            WHERE status = 'active'
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        return self._row_to_dict(await cursor.fetchone())

    async def create_oauth_signing_key(self, *, kid: str, private_key_pem: str, alg: str = "RS256") -> dict[str, Any]:
        await self.db.execute(
            """
            INSERT INTO oauth_signing_keys (kid, private_key_pem, alg, status)
            VALUES (?, ?, ?, 'active')
            """,
            (kid, private_key_pem, alg),
        )
        await self.db.commit()
        return await self.get_active_oauth_signing_key() or {}

    async def update_oauth_signing_key_private_material(self, *, kid: str, private_key_pem: str) -> None:
        await self.db.execute(
            """
            UPDATE oauth_signing_keys
            SET private_key_pem = ?
            WHERE kid = ?
            """,
            (private_key_pem, kid),
        )
        await self.db.commit()

    async def upsert_oauth_client(
        self,
        *,
        client_id: str,
        client_name: str,
        redirect_uris: list[str],
        scope: str,
        grant_types: list[str],
        response_types: list[str],
        token_endpoint_auth_method: str = "none",
        source: str = "dcr",
    ) -> dict[str, Any]:
        await self.db.execute(
            """
            INSERT INTO oauth_clients
              (client_id, client_name, redirect_uris, scope, grant_types, response_types, token_endpoint_auth_method, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
              client_name = excluded.client_name,
              redirect_uris = excluded.redirect_uris,
              scope = excluded.scope,
              grant_types = excluded.grant_types,
              response_types = excluded.response_types,
              token_endpoint_auth_method = excluded.token_endpoint_auth_method,
              source = excluded.source,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                client_id,
                client_name,
                self.dumps_json_array(redirect_uris),
                scope,
                self.dumps_json_array(grant_types),
                self.dumps_json_array(response_types),
                token_endpoint_auth_method,
                source,
            ),
        )
        await self.db.commit()
        return await self.get_oauth_client(client_id) or {}

    async def get_oauth_client(self, client_id: str, *, source: str | None = None) -> dict[str, Any] | None:
        where = "client_id = ?"
        params: list[Any] = [client_id]
        if source is not None:
            where += " AND source = ?"
            params.append(source)
        cursor = await self.db.execute(
            f"""
            SELECT client_id, client_name, redirect_uris, scope, grant_types, response_types,
                   token_endpoint_auth_method, source, created_at, updated_at
            FROM oauth_clients
            WHERE {where}
            """,
            params,
        )
        return self._row_to_dict(await cursor.fetchone(), json_fields=("redirect_uris", "grant_types", "response_types"))

    async def create_oauth_authorization_code(
        self,
        *,
        code_hash: str,
        client_id: str,
        redirect_uri: str,
        resource: str,
        scope: str,
        code_challenge: str,
        expires_at: float,
    ) -> dict[str, Any]:
        code_id = new_id("ocode")
        await self.db.execute(
            """
            INSERT INTO oauth_authorization_codes
              (id, code_hash, client_id, redirect_uri, resource, scope, code_challenge, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (code_id, code_hash, client_id, redirect_uri, resource, scope, code_challenge, expires_at),
        )
        await self.db.commit()
        return {"id": code_id, "code_hash": code_hash}

    async def consume_oauth_authorization_code(
        self,
        *,
        code_hash: str,
        client_id: str,
        redirect_uri: str,
        resource: str,
        now: float,
    ) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            UPDATE oauth_authorization_codes
            SET used_at = ?
            WHERE code_hash = ?
              AND client_id = ?
              AND redirect_uri = ?
              AND resource = ?
              AND used_at IS NULL
              AND expires_at > ?
            RETURNING id, code_hash, client_id, redirect_uri, resource, scope, code_challenge, expires_at, used_at, created_at
            """,
            (now, code_hash, client_id, redirect_uri, resource, now),
        )
        row = await cursor.fetchone()
        await self.db.commit()
        return self._row_to_dict(row)

    async def create_oauth_refresh_token(
        self,
        *,
        token_hash: str,
        client_id: str,
        external_connection_id: str,
        resource: str,
        scope: str,
        expires_at: float,
        family_id: str,
        parent_hash: str | None = None,
        issued_at: float | None = None,
    ) -> dict[str, Any]:
        refresh_id = new_id("rtok")
        await self.db.execute(
            """
            INSERT INTO oauth_refresh_tokens
              (id, token_hash, client_id, external_connection_id, resource, scope, expires_at, family_id, parent_hash, issued_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (refresh_id, token_hash, client_id, external_connection_id, resource, scope, expires_at, family_id, parent_hash, issued_at),
        )
        await self.db.commit()
        return await self.find_oauth_refresh_token_by_hash(token_hash) or {}

    async def find_oauth_refresh_token_by_hash(self, token_hash: str) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            SELECT id, token_hash, client_id, external_connection_id, resource, scope, expires_at,
                   family_id, parent_hash, issued_at, used_at, revoked_at, revoked_reason, created_at
            FROM oauth_refresh_tokens
            WHERE token_hash = ?
            """,
            (token_hash,),
        )
        return self._row_to_dict(await cursor.fetchone())

    async def mark_oauth_refresh_token_rotated(self, *, token_hash: str, now: float) -> bool:
        cursor = await self.db.execute(
            """
            UPDATE oauth_refresh_tokens
            SET used_at = ?, revoked_at = ?, revoked_reason = 'rotated'
            WHERE token_hash = ? AND used_at IS NULL AND revoked_at IS NULL
            """,
            (now, now, token_hash),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def revoke_oauth_refresh_token(self, *, token_hash: str, reason: str, now: float) -> bool:
        cursor = await self.db.execute(
            """
            UPDATE oauth_refresh_tokens
            SET revoked_at = COALESCE(revoked_at, ?), revoked_reason = COALESCE(revoked_reason, ?)
            WHERE token_hash = ?
            """,
            (now, reason, token_hash),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def revoke_oauth_refresh_family(self, *, family_id: str, reason: str, now: float) -> int:
        cursor = await self.db.execute(
            """
            UPDATE oauth_refresh_tokens
            SET revoked_at = COALESCE(revoked_at, ?), revoked_reason = ?
            WHERE family_id = ?
            """,
            (now, reason, family_id),
        )
        await self.db.commit()
        return cursor.rowcount

    async def upsert_oauth_revoked_access_jti(self, *, jti: str, expires_at: float, now: float | None = None) -> None:
        await self.db.execute(
            """
            INSERT INTO oauth_revoked_access_tokens (jti, expires_at, revoked_at)
            VALUES (?, ?, ?)
            ON CONFLICT(jti) DO UPDATE SET
              expires_at = excluded.expires_at,
              revoked_at = excluded.revoked_at
            """,
            (jti, expires_at, now if now is not None else 0.0),
        )
        await self.db.commit()

    async def is_oauth_access_jti_revoked(self, *, jti: str, now: float) -> bool:
        await self.db.execute("DELETE FROM oauth_revoked_access_tokens WHERE expires_at <= ?", (now,))
        cursor = await self.db.execute(
            "SELECT 1 AS revoked FROM oauth_revoked_access_tokens WHERE jti = ? AND expires_at > ?",
            (jti, now),
        )
        row = await cursor.fetchone()
        await self.db.commit()
        return row is not None

    async def upsert_oauth_cimd_client(
        self,
        *,
        client_id: str,
        client_name: str,
        redirect_uris: list[str],
        scope: str,
        grant_types: list[str],
        response_types: list[str],
        token_endpoint_auth_method: str = "none",
        cached_until: float,
    ) -> dict[str, Any]:
        await self.upsert_oauth_client(
            client_id=client_id,
            client_name=client_name,
            redirect_uris=redirect_uris,
            scope=scope,
            grant_types=grant_types,
            response_types=response_types,
            token_endpoint_auth_method=token_endpoint_auth_method,
            source="cimd",
        )
        await self.db.execute(
            """
            INSERT INTO oauth_cimd_cache (client_id, cached_until)
            VALUES (?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
              cached_until = excluded.cached_until,
              updated_at = CURRENT_TIMESTAMP
            """,
            (client_id, cached_until),
        )
        await self.db.commit()
        return await self.get_oauth_client(client_id, source="cimd") or {}

    async def get_cached_oauth_cimd_client(self, *, client_id: str, now: float) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            SELECT c.client_id, c.client_name, c.redirect_uris, c.scope, c.grant_types, c.response_types,
                   c.token_endpoint_auth_method, c.source, c.created_at, c.updated_at, cache.cached_until
            FROM oauth_clients c
            JOIN oauth_cimd_cache cache ON cache.client_id = c.client_id
            WHERE c.client_id = ? AND c.source = 'cimd' AND cache.cached_until > ?
            """,
            (client_id, now),
        )
        return self._row_to_dict(await cursor.fetchone(), json_fields=("redirect_uris", "grant_types", "response_types"))

    # ------------------------------------------------------------------
    # MCP services / tools
    # ------------------------------------------------------------------
    async def create_mcp_service(
        self,
        *,
        name: str,
        slug: str,
        endpoint_url: str,
        auth_type: str = "none",
        description: str | None = None,
        category: str | None = None,
        logo_url: str | None = None,
        homepage_url: str | None = None,
        documentation_url: str | None = None,
        transport_type: str = "http",
        stdio_command: str | None = None,
        stdio_args: list[str] | None = None,
        stdio_env: dict[str, str] | None = None,
        stdio_cwd: str | None = None,
        stdio_idle_timeout_seconds: int = 300,
        status: str = "draft",
    ) -> dict[str, Any]:
        service_id = new_id("svc")
        await self.db.execute(
            """
            INSERT INTO mcp_services
              (id, owner_user_id, name, slug, description, endpoint_url, auth_type,
               category, logo_url, homepage_url, documentation_url, transport_type,
               stdio_command, stdio_args, stdio_env, stdio_cwd, stdio_idle_timeout_seconds, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                service_id,
                LOCAL_USER_ID,
                name,
                slug,
                description,
                endpoint_url,
                auth_type,
                category,
                logo_url,
                homepage_url,
                documentation_url,
                transport_type,
                stdio_command,
                self.dumps_json_array(stdio_args or []),
                self.dumps_json(stdio_env or {}),
                stdio_cwd,
                stdio_idle_timeout_seconds,
                status,
            ),
        )
        await self.log_audit(action="service.create", resource_type="mcp_service", resource_id=service_id)
        return await self.get_mcp_service(service_id) or {}

    async def get_mcp_service(self, service_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            SELECT s.*, COUNT(st.id) AS tool_count,
                   sc.status AS credential_status, sc.masked_value AS credential_masked
            FROM mcp_services s
            LEFT JOIN service_tools st ON st.service_id = s.id AND st.status = 'active'
            LEFT JOIN service_credentials sc ON sc.service_id = s.id AND sc.revoked_at IS NULL
            WHERE s.id = ? AND s.deleted_at IS NULL
            GROUP BY s.id
            """,
            (service_id,),
        )
        return self._row_to_dict(await cursor.fetchone(), json_fields=("validation_summary", "stdio_args", "stdio_env", "capabilities_json"))

    async def list_mcp_services(self, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE s.deleted_at IS NULL"
        params: list[Any] = []
        if status:
            where += " AND s.status = ?"
            params.append(status)
        params.append(limit)
        cursor = await self.db.execute(
            f"""
            SELECT s.id, s.name, s.slug, s.description, s.endpoint_url, s.auth_type, s.status,
                   s.category, s.logo_url, s.homepage_url, s.documentation_url, s.transport_type,
                   s.stdio_command, s.stdio_args, s.stdio_env, s.stdio_cwd,
                   s.stdio_idle_timeout_seconds, s.last_health_check_at, s.consecutive_failures,
                   s.circuit_open_until, s.capabilities_json,
                   s.risk_level, s.last_validated_at, s.updated_at, COUNT(st.id) AS tool_count
            FROM mcp_services s
            LEFT JOIN service_tools st ON st.service_id = s.id AND st.status = 'active'
            {where}
            GROUP BY s.id
            ORDER BY s.created_at DESC, s.id DESC
            LIMIT ?
            """,
            params,
        )
        return [
            self._row_to_dict(row, json_fields=("stdio_args", "stdio_env", "capabilities_json")) or {}
            for row in await cursor.fetchall()
        ]

    async def update_mcp_service(self, service_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {
            "name",
            "slug",
            "description",
            "endpoint_url",
            "auth_type",
            "status",
            "risk_level",
            "validation_summary",
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
            "last_health_check_at",
            "consecutive_failures",
            "circuit_open_until",
            "last_stdio_started_at",
            "last_stdio_used_at",
            "stdio_restart_count",
            "last_stdio_exit_code",
            "last_stdio_error",
            "last_stdio_stderr_tail",
            "capabilities_json",
        }
        fields: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "validation_summary":
                value = self.dumps_json(value or {})
            if key == "capabilities_json":
                value = self.dumps_json(value or {})
            if key == "stdio_args":
                value = self.dumps_json_array(value or [])
            if key == "stdio_env":
                value = self.dumps_json(value or {})
            fields.append(f"{key} = ?")
            values.append(value)
        if fields:
            fields.append("updated_at = CURRENT_TIMESTAMP")
            values.append(service_id)
            await self.db.execute(
                f"UPDATE mcp_services SET {', '.join(fields)} WHERE id = ? AND deleted_at IS NULL",
                values,
            )
            await self.db.commit()
        return await self.get_mcp_service(service_id)

    async def mark_service_validated(
        self,
        *,
        service_id: str,
        status: str,
        protocol_version: str | None = None,
        summary: dict[str, Any] | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        await self.db.execute(
            """
            UPDATE mcp_services
            SET status = ?, protocol_version = ?, validation_summary = ?, capabilities_json = ?,
                last_validated_at = CURRENT_TIMESTAMP, last_tool_refresh_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, protocol_version, self.dumps_json(summary or {}), self.dumps_json(capabilities or {}), service_id),
        )
        await self.db.commit()

    async def mark_service_health_probe(
        self,
        *,
        service_id: str,
        ok: bool,
        error_message: str | None = None,
        circuit_open_seconds: int = 30,
        failure_threshold: int = 3,
    ) -> None:
        if ok:
            await self.db.execute(
                """
                UPDATE mcp_services
                SET last_health_check_at = CURRENT_TIMESTAMP,
                    consecutive_failures = 0,
                    circuit_open_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND deleted_at IS NULL
                """,
                (service_id,),
            )
        else:
            await self.db.execute(
                """
                UPDATE mcp_services
                SET last_health_check_at = CURRENT_TIMESTAMP,
                    consecutive_failures = consecutive_failures + 1,
                    circuit_open_until = CASE
                        WHEN consecutive_failures + 1 >= ? THEN datetime('now', ?)
                        ELSE circuit_open_until
                    END,
                    validation_summary = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND deleted_at IS NULL
                """,
                (
                    failure_threshold,
                    f"+{max(1, int(circuit_open_seconds))} seconds",
                    self.dumps_json({"health_probe": {"status": "failed", "error": error_message or "probe_failed"}}),
                    service_id,
                ),
            )
        await self.db.commit()

    async def soft_delete_mcp_service(self, service_id: str) -> bool:
        cursor = await self.db.execute(
            """
            UPDATE mcp_services
            SET status = 'deleted', deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND deleted_at IS NULL
            """,
            (service_id,),
        )
        await self.db.execute(
            "UPDATE toolbox_items SET deleted_at = CURRENT_TIMESTAMP WHERE service_id = ? AND deleted_at IS NULL",
            (service_id,),
        )
        await self.log_audit(action="service.delete", resource_type="mcp_service", resource_id=service_id)
        return cursor.rowcount > 0

    async def replace_service_tools(self, service_id: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        await self.db.execute("UPDATE service_tools SET status = 'disabled', disabled_at = CURRENT_TIMESTAMP WHERE service_id = ?", (service_id,))
        saved: list[dict[str, Any]] = []
        service = await self.get_mcp_service(service_id)
        slug = service["slug"] if service else "service"
        for tool in tools:
            original_name = str(tool["original_name"])
            cursor = await self.db.execute(
                "SELECT id FROM service_tools WHERE service_id = ? AND original_name = ?",
                (service_id, original_name),
            )
            existing = await cursor.fetchone()
            tool_id = existing["id"] if existing else new_id("tool")
            values = (
                tool_id,
                service_id,
                original_name,
                tool.get("title"),
                tool.get("description"),
                self.dumps_json(tool.get("input_schema_json") or {}),
                self.dumps_json(tool.get("output_schema_json")) if tool.get("output_schema_json") is not None else None,
                self.dumps_json(tool.get("structured_output_schema_json"))
                if tool.get("structured_output_schema_json") is not None
                else None,
                self.dumps_json(tool.get("annotations") or {}),
                self.dumps_json_array(tool.get("icons_json") or []),
                tool["schema_hash"],
                tool.get("risk_level", "unknown"),
                self.dumps_json(tool.get("metadata_scan") or {}),
            )
            if existing:
                await self.db.execute(
                    """
                    UPDATE service_tools
                    SET title = ?, description = ?, input_schema_json = ?, output_schema_json = ?,
                        structured_output_schema_json = ?, annotations = ?, icons_json = ?, schema_hash = ?,
                        status = 'active', risk_level = ?, metadata_scan = ?, last_seen_at = CURRENT_TIMESTAMP,
                        cached_at = CURRENT_TIMESTAMP, disabled_at = NULL
                    WHERE id = ?
                    """,
                    values[3:] + (tool_id,),
                )
            else:
                await self.db.execute(
                    """
                    INSERT INTO service_tools
                      (id, service_id, original_name, title, description, input_schema_json, output_schema_json,
                       structured_output_schema_json, annotations, icons_json, schema_hash, risk_level, metadata_scan)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            exposed_name = tool.get("exposed_name") or f"{slug}.{original_name}"
            await self.db.execute(
                "UPDATE tool_aliases SET is_primary = 0, deprecated_at = CURRENT_TIMESTAMP WHERE service_tool_id = ? AND deprecated_at IS NULL",
                (tool_id,),
            )
            await self.db.execute(
                """
                INSERT INTO tool_aliases (id, service_tool_id, exposed_name, is_primary)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(exposed_name) WHERE deprecated_at IS NULL DO UPDATE SET
                  service_tool_id = excluded.service_tool_id,
                  is_primary = 1,
                  deprecated_at = NULL
                """,
                (new_id("tali"), tool_id, exposed_name),
            )
            saved_item = await self.get_service_tool(tool_id)
            if saved_item:
                saved.append(saved_item)
        await self.db.commit()
        return saved

    async def get_service_tool(self, tool_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            SELECT st.*, ta.exposed_name
            FROM service_tools st
            LEFT JOIN tool_aliases ta ON ta.service_tool_id = st.id AND ta.is_primary = 1 AND ta.deprecated_at IS NULL
            WHERE st.id = ?
            """,
            (tool_id,),
        )
        return self._row_to_dict(
            await cursor.fetchone(),
            json_fields=("input_schema_json", "output_schema_json", "structured_output_schema_json", "annotations", "icons_json", "metadata_scan"),
        )

    async def list_service_tools(self, service_id: str) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            """
            SELECT st.*, ta.exposed_name
            FROM service_tools st
            LEFT JOIN tool_aliases ta ON ta.service_tool_id = st.id AND ta.is_primary = 1 AND ta.deprecated_at IS NULL
            WHERE st.service_id = ? AND st.status = 'active'
            ORDER BY st.original_name ASC
            """,
            (service_id,),
        )
        return [
            self._row_to_dict(
                row,
                json_fields=(
                    "input_schema_json",
                    "output_schema_json",
                    "structured_output_schema_json",
                    "annotations",
                    "icons_json",
                    "metadata_scan",
                ),
            )
            or {}
            for row in await cursor.fetchall()
        ]

    async def replace_service_resources(self, service_id: str, resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        await self.db.execute("UPDATE service_resources SET status = 'disabled', disabled_at = CURRENT_TIMESTAMP WHERE service_id = ?", (service_id,))
        saved: list[dict[str, Any]] = []
        for resource in resources:
            uri = resource.get("uri")
            if not isinstance(uri, str) or not uri:
                continue
            cursor = await self.db.execute("SELECT id FROM service_resources WHERE service_id = ? AND uri = ?", (service_id, uri))
            existing = await cursor.fetchone()
            resource_id = existing["id"] if existing else new_id("res")
            values = (
                resource_id,
                service_id,
                uri,
                resource.get("name") if isinstance(resource.get("name"), str) else None,
                resource.get("title") if isinstance(resource.get("title"), str) else None,
                resource.get("description") if isinstance(resource.get("description"), str) else None,
                resource.get("mimeType") if isinstance(resource.get("mimeType"), str) else resource.get("mime_type"),
                self.dumps_json(resource.get("annotations") or {}),
                self.dumps_json(resource),
            )
            if existing:
                await self.db.execute(
                    """
                    UPDATE service_resources
                    SET name = ?, title = ?, description = ?, mime_type = ?, annotations = ?, metadata_json = ?,
                        status = 'active', last_seen_at = CURRENT_TIMESTAMP, cached_at = CURRENT_TIMESTAMP, disabled_at = NULL
                    WHERE id = ?
                    """,
                    values[3:] + (resource_id,),
                )
            else:
                await self.db.execute(
                    """
                    INSERT INTO service_resources
                      (id, service_id, uri, name, title, description, mime_type, annotations, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            saved_item = await self.get_service_resource(resource_id)
            if saved_item:
                saved.append(saved_item)
        await self.apply_resource_shadow_policy(service_id)
        await self.db.commit()
        return saved

    async def apply_resource_shadow_policy(self, refreshed_service_id: str) -> None:
        cursor = await self.db.execute(
            """
            SELECT current.id AS active_resource_id, current.uri
            FROM service_resources current
            JOIN mcp_services s ON s.id = current.service_id AND s.deleted_at IS NULL AND s.status = 'active'
            WHERE current.service_id = ? AND current.status = 'active'
              AND EXISTS (
                SELECT 1
                FROM service_resources other
                JOIN mcp_services os ON os.id = other.service_id AND os.deleted_at IS NULL AND os.status = 'active'
                WHERE other.uri = current.uri
                  AND other.status = 'active'
                  AND other.service_id != current.service_id
              )
            """,
            (refreshed_service_id,),
        )
        for row in await cursor.fetchall():
            shadow_cursor = await self.db.execute(
                """
                SELECT id, service_id
                FROM service_resources
                WHERE uri = ? AND service_id != ? AND status = 'active'
                """,
                (row["uri"], refreshed_service_id),
            )
            shadowed_rows = await shadow_cursor.fetchall()
            if not shadowed_rows:
                continue
            await self.db.execute(
                """
                UPDATE service_resources
                SET status = 'deprecated', disabled_at = CURRENT_TIMESTAMP, cached_at = CURRENT_TIMESTAMP
                WHERE uri = ? AND service_id != ? AND status = 'active'
                """,
                (row["uri"], refreshed_service_id),
            )
            for shadowed in shadowed_rows:
                await self.log_audit(
                    action="resource.shadow",
                    resource_type="service_resource",
                    resource_id=shadowed["id"],
                    metadata={
                        "uri": row["uri"],
                        "shadowed_service_id": shadowed["service_id"],
                        "active_service_id": refreshed_service_id,
                        "active_resource_id": row["active_resource_id"],
                    },
                )

    async def replace_service_resource_templates(self, service_id: str, templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        await self.db.execute("UPDATE service_resource_templates SET status = 'disabled', disabled_at = CURRENT_TIMESTAMP WHERE service_id = ?", (service_id,))
        saved: list[dict[str, Any]] = []
        for template in templates:
            uri_template = template.get("uriTemplate") or template.get("uri_template")
            if not isinstance(uri_template, str) or not uri_template:
                continue
            cursor = await self.db.execute(
                "SELECT id FROM service_resource_templates WHERE service_id = ? AND uri_template = ?",
                (service_id, uri_template),
            )
            existing = await cursor.fetchone()
            template_id = existing["id"] if existing else new_id("restpl")
            values = (
                template_id,
                service_id,
                uri_template,
                template.get("name") if isinstance(template.get("name"), str) else None,
                template.get("title") if isinstance(template.get("title"), str) else None,
                template.get("description") if isinstance(template.get("description"), str) else None,
                template.get("mimeType") if isinstance(template.get("mimeType"), str) else template.get("mime_type"),
                self.dumps_json(template.get("annotations") or {}),
                self.dumps_json(template),
            )
            if existing:
                await self.db.execute(
                    """
                    UPDATE service_resource_templates
                    SET name = ?, title = ?, description = ?, mime_type = ?, annotations = ?, metadata_json = ?,
                        status = 'active', last_seen_at = CURRENT_TIMESTAMP, cached_at = CURRENT_TIMESTAMP, disabled_at = NULL
                    WHERE id = ?
                    """,
                    values[3:] + (template_id,),
                )
            else:
                await self.db.execute(
                    """
                    INSERT INTO service_resource_templates
                      (id, service_id, uri_template, name, title, description, mime_type, annotations, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            saved_item = await self.get_service_resource_template(template_id)
            if saved_item:
                saved.append(saved_item)
        await self.db.commit()
        return saved

    async def replace_service_prompts(self, service_id: str, prompts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        await self.db.execute("UPDATE service_prompts SET status = 'disabled', disabled_at = CURRENT_TIMESTAMP WHERE service_id = ?", (service_id,))
        saved: list[dict[str, Any]] = []
        for prompt in prompts:
            name = prompt.get("name")
            if not isinstance(name, str) or not name:
                continue
            cursor = await self.db.execute("SELECT id FROM service_prompts WHERE service_id = ? AND name = ?", (service_id, name))
            existing = await cursor.fetchone()
            prompt_id = existing["id"] if existing else new_id("prm")
            values = (
                prompt_id,
                service_id,
                name,
                prompt.get("title") if isinstance(prompt.get("title"), str) else None,
                prompt.get("description") if isinstance(prompt.get("description"), str) else None,
                self.dumps_json_array(prompt.get("arguments") or []),
                self.dumps_json(prompt),
            )
            if existing:
                await self.db.execute(
                    """
                    UPDATE service_prompts
                    SET title = ?, description = ?, arguments_json = ?, metadata_json = ?,
                        status = 'active', last_seen_at = CURRENT_TIMESTAMP, cached_at = CURRENT_TIMESTAMP, disabled_at = NULL
                    WHERE id = ?
                    """,
                    values[3:] + (prompt_id,),
                )
            else:
                await self.db.execute(
                    """
                    INSERT INTO service_prompts
                      (id, service_id, name, title, description, arguments_json, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            saved_item = await self.get_service_prompt(prompt_id)
            if saved_item:
                saved.append(saved_item)
        await self.db.commit()
        return saved

    async def get_service_resource(self, resource_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute("SELECT * FROM service_resources WHERE id = ?", (resource_id,))
        return self._row_to_dict(await cursor.fetchone(), json_fields=("annotations", "metadata_json"))

    async def get_service_resource_template(self, template_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute("SELECT * FROM service_resource_templates WHERE id = ?", (template_id,))
        return self._row_to_dict(await cursor.fetchone(), json_fields=("annotations", "metadata_json"))

    async def get_service_prompt(self, prompt_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute("SELECT * FROM service_prompts WHERE id = ?", (prompt_id,))
        return self._row_to_dict(await cursor.fetchone(), json_fields=("arguments_json", "metadata_json"))

    # ------------------------------------------------------------------
    # Toolbox / catalog
    # ------------------------------------------------------------------
    async def list_toolboxes(self, limit: int = 50) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            """
            SELECT t.id, t.name, t.slug, t.is_default, t.visibility, t.created_at, t.updated_at,
                   COUNT(tbi.id) AS item_count
            FROM toolboxes t
            LEFT JOIN toolbox_items tbi ON tbi.toolbox_id = t.id AND tbi.deleted_at IS NULL
            WHERE t.deleted_at IS NULL
            GROUP BY t.id
            ORDER BY t.is_default DESC, t.created_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        items = [dict(row) for row in await cursor.fetchall()]
        for item in items:
            item["enabled"] = bool(item.get("enabled", True))
        return items

    async def add_toolbox_item(self, toolbox_id: str, service_id: str, enabled: bool = True) -> dict[str, Any]:
        cursor = await self.db.execute(
            """
            SELECT id FROM toolbox_items
            WHERE toolbox_id = ? AND service_id = ? AND deleted_at IS NULL
            """,
            (toolbox_id, service_id),
        )
        existing = await cursor.fetchone()
        if existing:
            await self.db.execute(
                "UPDATE toolbox_items SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (1 if enabled else 0, existing["id"]),
            )
            item_id = existing["id"]
        else:
            item_id = new_id("tbi")
            await self.db.execute(
                """
                INSERT INTO toolbox_items (id, toolbox_id, service_id, enabled, added_by_user_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (item_id, toolbox_id, service_id, 1 if enabled else 0, LOCAL_USER_ID),
            )
        await self.log_audit(action="toolbox_item.upsert", resource_type="toolbox_item", resource_id=item_id)
        item = await self.get_toolbox_item(item_id)
        return item or {}

    async def get_toolbox_item(self, item_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            SELECT tbi.id, tbi.toolbox_id, tbi.service_id, tbi.enabled, tbi.position, tbi.created_at, tbi.updated_at,
                   s.name AS service_name, s.slug AS service_slug, s.status AS service_status
            FROM toolbox_items tbi
            JOIN mcp_services s ON s.id = tbi.service_id
            WHERE tbi.id = ? AND tbi.deleted_at IS NULL
            """,
            (item_id,),
        )
        return self._row_to_dict(await cursor.fetchone())

    async def list_toolbox_items(self, toolbox_id: str) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            """
            SELECT tbi.id, tbi.toolbox_id, tbi.service_id, tbi.enabled, tbi.position, tbi.created_at, tbi.updated_at,
                   s.name AS service_name, s.slug AS service_slug, s.status AS service_status,
                   COUNT(st.id) AS tool_count
            FROM toolbox_items tbi
            JOIN mcp_services s ON s.id = tbi.service_id
            LEFT JOIN service_tools st ON st.service_id = s.id AND st.status = 'active'
            WHERE tbi.toolbox_id = ? AND tbi.deleted_at IS NULL
            GROUP BY tbi.id
            ORDER BY tbi.position ASC, tbi.created_at ASC
            """,
            (toolbox_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def update_toolbox_item(self, item_id: str, *, enabled: bool) -> dict[str, Any] | None:
        await self.db.execute(
            "UPDATE toolbox_items SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
            (1 if enabled else 0, item_id),
        )
        await self.db.commit()
        return await self.get_toolbox_item(item_id)

    async def delete_toolbox_item(self, item_id: str) -> bool:
        cursor = await self.db.execute(
            "UPDATE toolbox_items SET deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
            (item_id,),
        )
        await self.log_audit(action="toolbox_item.delete", resource_type="toolbox_item", resource_id=item_id)
        return cursor.rowcount > 0

    async def list_tool_overrides(self, service_id: str, toolbox_id: str = DEFAULT_TOOLBOX_ID) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            """
            SELECT
              COALESCE(tto.id, '') AS id,
              ? AS toolbox_id,
              st.service_id,
              st.id AS service_tool_id,
              ta.exposed_name,
              COALESCE(tto.enabled, 1) AS enabled,
              COALESCE(tto.permission_level, 'callable') AS permission_level,
              COALESCE(tto.updated_at, st.cached_at) AS updated_at
            FROM service_tools st
            JOIN tool_aliases ta ON ta.service_tool_id = st.id AND ta.is_primary = 1 AND ta.deprecated_at IS NULL
            LEFT JOIN toolbox_tool_overrides tto ON tto.toolbox_id = ? AND tto.service_tool_id = st.id
            WHERE st.service_id = ? AND st.status = 'active'
            ORDER BY st.original_name ASC
            """,
            (toolbox_id, toolbox_id, service_id),
        )
        items = [dict(row) for row in await cursor.fetchall()]
        for item in items:
            item["enabled"] = bool(item.get("enabled", True))
        return items

    async def upsert_tool_override(
        self,
        *,
        service_id: str,
        service_tool_id: str,
        enabled: bool,
        permission_level: str,
        toolbox_id: str = DEFAULT_TOOLBOX_ID,
    ) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            SELECT id FROM service_tools
            WHERE id = ? AND service_id = ? AND status = 'active'
            """,
            (service_tool_id, service_id),
        )
        if await cursor.fetchone() is None:
            return None

        override_id = new_id("tto")
        await self.db.execute(
            """
            INSERT INTO toolbox_tool_overrides
              (id, toolbox_id, service_id, service_tool_id, enabled, permission_level)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(toolbox_id, service_tool_id) DO UPDATE SET
              enabled = excluded.enabled,
              permission_level = excluded.permission_level,
              updated_at = CURRENT_TIMESTAMP
            """,
            (override_id, toolbox_id, service_id, service_tool_id, 1 if enabled else 0, permission_level),
        )
        await self.log_audit(
            action="tool_permission.upsert",
            resource_type="service_tool",
            resource_id=service_tool_id,
            metadata={"service_id": service_id, "toolbox_id": toolbox_id, "enabled": enabled, "permission_level": permission_level},
        )
        items = await self.list_tool_overrides(service_id, toolbox_id=toolbox_id)
        return next((item for item in items if item["service_tool_id"] == service_tool_id), None)

    async def get_catalog_tools(self, toolbox_id: str = DEFAULT_TOOLBOX_ID) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            """
            SELECT st.id AS service_tool_id, st.service_id, st.original_name, st.title, st.description,
                   st.input_schema_json, st.output_schema_json, st.structured_output_schema_json,
                   st.annotations, st.icons_json, st.schema_hash, st.risk_level, st.metadata_scan,
                   ta.exposed_name, s.slug AS service_slug, s.endpoint_url, s.auth_type, s.status AS service_status,
                   s.transport_type, s.stdio_command, s.stdio_args, s.stdio_env, s.stdio_cwd,
                   s.stdio_idle_timeout_seconds,
                   COALESCE(tto.enabled, 1) AS override_enabled,
                   COALESCE(tto.permission_level, 'callable') AS permission_level
            FROM toolbox_items tbi
            JOIN mcp_services s ON s.id = tbi.service_id AND s.deleted_at IS NULL
            JOIN service_tools st ON st.service_id = s.id AND st.status = 'active'
            JOIN tool_aliases ta ON ta.service_tool_id = st.id AND ta.is_primary = 1 AND ta.deprecated_at IS NULL
            LEFT JOIN toolbox_tool_overrides tto ON tto.toolbox_id = tbi.toolbox_id AND tto.service_tool_id = st.id
            WHERE tbi.toolbox_id = ?
              AND tbi.deleted_at IS NULL
              AND tbi.enabled = 1
              AND s.status = 'active'
            ORDER BY tbi.position ASC, s.slug ASC, st.original_name ASC
            """,
            (toolbox_id,),
        )
        items = [
            self._row_to_dict(
                row,
                json_fields=(
                    "input_schema_json",
                    "output_schema_json",
                    "structured_output_schema_json",
                    "annotations",
                    "icons_json",
                    "metadata_scan",
                    "stdio_args",
                    "stdio_env",
                ),
            )
            or {}
            for row in await cursor.fetchall()
        ]
        for item in items:
            item["override_enabled"] = bool(item.get("override_enabled", True))
        return items

    async def list_catalog_resources(self, toolbox_id: str = DEFAULT_TOOLBOX_ID) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            """
            SELECT sr.*, s.slug AS service_slug, s.endpoint_url, s.transport_type,
                   s.stdio_command, s.stdio_args, s.stdio_env, s.stdio_cwd, s.stdio_idle_timeout_seconds
            FROM toolbox_items tbi
            JOIN mcp_services s ON s.id = tbi.service_id AND s.deleted_at IS NULL AND s.status = 'active'
            JOIN service_resources sr ON sr.service_id = s.id AND sr.status = 'active'
            WHERE tbi.toolbox_id = ? AND tbi.deleted_at IS NULL AND tbi.enabled = 1
            ORDER BY tbi.position ASC, s.slug ASC, sr.name ASC, sr.uri ASC
            """,
            (toolbox_id,),
        )
        return [
            self._row_to_dict(row, json_fields=("annotations", "metadata_json", "stdio_args", "stdio_env")) or {}
            for row in await cursor.fetchall()
        ]

    async def list_catalog_resource_templates(self, toolbox_id: str = DEFAULT_TOOLBOX_ID) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            """
            SELECT srt.*, s.slug AS service_slug
            FROM toolbox_items tbi
            JOIN mcp_services s ON s.id = tbi.service_id AND s.deleted_at IS NULL AND s.status = 'active'
            JOIN service_resource_templates srt ON srt.service_id = s.id AND srt.status = 'active'
            WHERE tbi.toolbox_id = ? AND tbi.deleted_at IS NULL AND tbi.enabled = 1
            ORDER BY tbi.position ASC, s.slug ASC, srt.name ASC, srt.uri_template ASC
            """,
            (toolbox_id,),
        )
        return [self._row_to_dict(row, json_fields=("annotations", "metadata_json")) or {} for row in await cursor.fetchall()]

    async def list_catalog_prompts(self, toolbox_id: str = DEFAULT_TOOLBOX_ID) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            """
            SELECT sp.*, s.slug AS service_slug, s.endpoint_url, s.transport_type,
                   s.stdio_command, s.stdio_args, s.stdio_env, s.stdio_cwd, s.stdio_idle_timeout_seconds
            FROM toolbox_items tbi
            JOIN mcp_services s ON s.id = tbi.service_id AND s.deleted_at IS NULL AND s.status = 'active'
            JOIN service_prompts sp ON sp.service_id = s.id AND sp.status = 'active'
            WHERE tbi.toolbox_id = ? AND tbi.deleted_at IS NULL AND tbi.enabled = 1
            ORDER BY tbi.position ASC, s.slug ASC, sp.name ASC
            """,
            (toolbox_id,),
        )
        return [
            self._row_to_dict(row, json_fields=("arguments_json", "metadata_json", "stdio_args", "stdio_env")) or {}
            for row in await cursor.fetchall()
        ]

    async def get_catalog_resource_by_uri(self, uri: str, toolbox_id: str = DEFAULT_TOOLBOX_ID) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            SELECT sr.*, s.slug AS service_slug, s.endpoint_url, s.transport_type,
                   s.stdio_command, s.stdio_args, s.stdio_env, s.stdio_cwd, s.stdio_idle_timeout_seconds
            FROM toolbox_items tbi
            JOIN mcp_services s ON s.id = tbi.service_id AND s.deleted_at IS NULL AND s.status = 'active'
            JOIN service_resources sr ON sr.service_id = s.id AND sr.status = 'active'
            WHERE tbi.toolbox_id = ? AND tbi.deleted_at IS NULL AND tbi.enabled = 1 AND sr.uri = ?
            ORDER BY tbi.position ASC, s.slug ASC
            LIMIT 2
            """,
            (toolbox_id, uri),
        )
        rows = await cursor.fetchall()
        if len(rows) != 1:
            return None
        return self._row_to_dict(rows[0], json_fields=("annotations", "metadata_json", "stdio_args", "stdio_env"))

    async def get_catalog_prompt_by_exposed_name(self, exposed_name: str, toolbox_id: str = DEFAULT_TOOLBOX_ID) -> dict[str, Any] | None:
        if "." not in exposed_name:
            return None
        service_slug, prompt_name = exposed_name.split(".", 1)
        cursor = await self.db.execute(
            """
            SELECT sp.*, s.slug AS service_slug, s.endpoint_url, s.transport_type,
                   s.stdio_command, s.stdio_args, s.stdio_env, s.stdio_cwd, s.stdio_idle_timeout_seconds
            FROM toolbox_items tbi
            JOIN mcp_services s ON s.id = tbi.service_id AND s.deleted_at IS NULL AND s.status = 'active'
            JOIN service_prompts sp ON sp.service_id = s.id AND sp.status = 'active'
            WHERE tbi.toolbox_id = ? AND tbi.deleted_at IS NULL AND tbi.enabled = 1
              AND s.slug = ? AND sp.name = ?
            LIMIT 1
            """,
            (toolbox_id, service_slug, prompt_name),
        )
        return self._row_to_dict(await cursor.fetchone(), json_fields=("arguments_json", "metadata_json", "stdio_args", "stdio_env"))

    # ------------------------------------------------------------------
    # Credentials / jobs
    # ------------------------------------------------------------------
    async def upsert_service_credential(
        self,
        *,
        service_id: str,
        credential_type: str,
        secret_ref: str,
        masked_value: str,
        header_name: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        await self.db.execute(
            "UPDATE service_credentials SET revoked_at = CURRENT_TIMESTAMP, status = 'revoked' WHERE service_id = ? AND revoked_at IS NULL",
            (service_id,),
        )
        credential_id = new_id("cred")
        await self.db.execute(
            """
            INSERT INTO service_credentials
              (id, service_id, owner_user_id, credential_type, secret_ref, header_name, masked_value, scopes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                credential_id,
                service_id,
                LOCAL_USER_ID,
                credential_type,
                secret_ref,
                header_name,
                masked_value,
                self.dumps_json_array(scopes or []),
            ),
        )
        await self.log_audit(action="credential.put", resource_type="mcp_service", resource_id=service_id)
        return await self.get_service_credential(service_id) or {}

    async def get_service_credential(self, service_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            """
            SELECT id, service_id, credential_type, secret_ref, header_name, masked_value, scopes,
                   status, last_error_code, last_error_message, expires_at, rotated_at, revoked_at, created_at, updated_at
            FROM service_credentials
            WHERE service_id = ? AND revoked_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (service_id,),
        )
        return self._row_to_dict(await cursor.fetchone(), json_fields=("scopes",))

    async def revoke_service_credential(self, service_id: str) -> bool:
        cursor = await self.db.execute(
            """
            UPDATE service_credentials
            SET status = 'revoked', revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP
            WHERE service_id = ? AND revoked_at IS NULL
            """,
            (service_id,),
        )
        await self.log_audit(action="credential.delete", resource_type="mcp_service", resource_id=service_id)
        return cursor.rowcount > 0

    async def create_job(self, *, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        job_id = new_id("job")
        await self.db.execute(
            "INSERT INTO jobs (id, kind, payload) VALUES (?, ?, ?)",
            (job_id, kind, self.dumps_json(payload or {})),
        )
        await self.db.commit()
        return await self.get_job(job_id) or {}

    async def update_job(
        self,
        job_id: str,
        *,
        status: str,
        progress: float | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        await self.db.execute(
            """
            UPDATE jobs
            SET status = ?, progress = COALESCE(?, progress), result = COALESCE(?, result),
                error = COALESCE(?, error), started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                finished_at = CASE WHEN ? IN ('success', 'failed', 'cancelled') THEN CURRENT_TIMESTAMP ELSE finished_at END
            WHERE id = ?
            """,
            (
                status,
                progress,
                self.dumps_json(result) if result is not None else None,
                self.dumps_json(error) if error is not None else None,
                status,
                job_id,
            ),
        )
        await self.db.commit()
        return await self.get_job(job_id)

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        cursor = await self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return self._row_to_dict(await cursor.fetchone(), json_fields=("payload", "result", "error"))

    async def mark_stuck_jobs_failed(self, *, max_age_seconds: int, now_epoch: float | None = None) -> int:
        now_expr = "strftime('%s','now')" if now_epoch is None else "?"
        params: list[Any] = []
        if now_epoch is not None:
            params.append(float(now_epoch))
        params.append(max(1, int(max_age_seconds)))
        cursor = await self.db.execute(
            f"""
            UPDATE jobs
            SET status = 'failed',
                error = ?,
                finished_at = CURRENT_TIMESTAMP
            WHERE status IN ('queued', 'running')
              AND ({now_expr} - strftime('%s', COALESCE(started_at, created_at))) >= ?
            """,
            [
                self.dumps_json({"code": "stuck_job_reaped", "message": "Job exceeded max runtime and was marked failed"}),
                *params,
            ],
        )
        await self.db.commit()
        return int(cursor.rowcount or 0)
