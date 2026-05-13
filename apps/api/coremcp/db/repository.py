from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

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
        if str(self.database_path) != ":memory:":
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.database_path))
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
        await self.db.executescript(SCHEMA_SQL)
        await self._ensure_legacy_columns()
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

    async def _ensure_legacy_columns(self) -> None:
        """Add nullable columns when a P0 local DB already exists.

        SQLite cannot add every CHECK/NOT NULL constraint after the fact. For the
        personal gateway this additive migration is enough to keep local smoke DBs
        usable while Alembic covers fresh installs precisely.
        """

        columns = await self._table_columns("tool_invocations")
        additions = {
            "request_id": "TEXT",
            "external_connection_id": "TEXT",
            "service_id": "TEXT",
            "service_tool_id": "TEXT",
            "exposed_tool_name": "TEXT",
            "downstream_tool_name": "TEXT",
            "downstream_latency_ms": "INTEGER",
            "error_message": "TEXT",
            "input_size_bytes": "INTEGER",
            "output_size_bytes": "INTEGER",
            "protocol_version": "TEXT",
            "idempotency_key": "TEXT",
            "client_ip": "TEXT",
            "user_agent": "TEXT",
        }
        for name, ddl in additions.items():
            if name not in columns:
                await self.db.execute(f"ALTER TABLE tool_invocations ADD COLUMN {name} {ddl}")

        service_columns = await self._table_columns("mcp_services")
        service_additions = {
            "category": "TEXT",
            "logo_url": "TEXT",
            "homepage_url": "TEXT",
            "documentation_url": "TEXT",
        }
        for name, ddl in service_additions.items():
            if name not in service_columns:
                await self.db.execute(f"ALTER TABLE mcp_services ADD COLUMN {name} {ddl}")

    async def _table_columns(self, table_name: str) -> set[str]:
        cursor = await self.db.execute(f"PRAGMA table_info({table_name})")
        rows = await cursor.fetchall()
        return {str(row["name"]) for row in rows}

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
        protocol_version: str | None = None,
        scopes: list[str] | None = None,
        created_ip: str | None = None,
        created_user_agent: str | None = None,
    ) -> dict[str, Any]:
        connection_id = new_id("ext")
        await self.db.execute(
            """
            INSERT INTO external_connections
              (id, user_id, toolbox_id, client_type, client_name, protocol_version, scopes, created_ip, created_user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                connection_id,
                LOCAL_USER_ID,
                toolbox_id or DEFAULT_TOOLBOX_ID,
                client_type,
                client_name,
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
            SELECT id, toolbox_id, client_type, client_name, protocol_version, status, scopes,
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
            SELECT id, toolbox_id, client_type, client_name, protocol_version, status, scopes,
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
        status: str = "draft",
    ) -> dict[str, Any]:
        service_id = new_id("svc")
        await self.db.execute(
            """
            INSERT INTO mcp_services
              (id, owner_user_id, name, slug, description, endpoint_url, auth_type,
               category, logo_url, homepage_url, documentation_url, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        return self._row_to_dict(await cursor.fetchone(), json_fields=("validation_summary",))

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
                   s.category, s.logo_url, s.homepage_url, s.documentation_url,
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
        return [dict(row) for row in await cursor.fetchall()]

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
        }
        fields: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "validation_summary":
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
    ) -> None:
        await self.db.execute(
            """
            UPDATE mcp_services
            SET status = ?, protocol_version = ?, validation_summary = ?,
                last_validated_at = CURRENT_TIMESTAMP, last_tool_refresh_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, protocol_version, self.dumps_json(summary or {}), service_id),
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
                ),
            )
            or {}
            for row in await cursor.fetchall()
        ]
        for item in items:
            item["override_enabled"] = bool(item.get("override_enabled", True))
        return items

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


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE DEFAULT 'me@local',
  name TEXT NOT NULL DEFAULT 'Personal',
  avatar_url TEXT,
  locale TEXT NOT NULL DEFAULT 'ko',
  is_active INTEGER NOT NULL DEFAULT 1,
  bootstrap_completed_at TIMESTAMP,
  last_login_at TIMESTAMP,
  workspace_id TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS toolboxes (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  workspace_id TEXT,
  name TEXT NOT NULL,
  slug TEXT,
  is_default INTEGER NOT NULL DEFAULT 0,
  visibility TEXT NOT NULL DEFAULT 'private',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_default_toolbox_per_user ON toolboxes(owner_user_id) WHERE is_default = 1 AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_toolboxes_owner ON toolboxes(owner_user_id);

CREATE TABLE IF NOT EXISTS mcp_services (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  workspace_id TEXT,
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  description TEXT,
  endpoint_url TEXT NOT NULL,
  auth_type TEXT NOT NULL DEFAULT 'none' CHECK (auth_type IN ('none', 'bearer_token', 'api_key_header', 'oauth_delegated', 'service_account')),
  visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'unlisted', 'public', 'review_pending', 'rejected')),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'validating', 'active', 'error', 'disabled', 'auth_required', 'deleted')),
  category TEXT,
  logo_url TEXT,
  homepage_url TEXT,
  documentation_url TEXT,
  risk_level TEXT NOT NULL DEFAULT 'unknown' CHECK (risk_level IN ('unknown', 'low', 'medium', 'high', 'critical')),
  validation_summary TEXT NOT NULL DEFAULT '{}',
  last_validated_at TIMESTAMP,
  last_tool_refresh_at TIMESTAMP,
  protocol_version TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mcp_services_owner_slug_active ON mcp_services(owner_user_id, slug) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_mcp_services_owner ON mcp_services(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_mcp_services_status ON mcp_services(status);

CREATE TABLE IF NOT EXISTS service_tools (
  id TEXT PRIMARY KEY,
  service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  original_name TEXT NOT NULL,
  title TEXT,
  description TEXT,
  input_schema_json TEXT NOT NULL DEFAULT '{}',
  output_schema_json TEXT,
  structured_output_schema_json TEXT,
  annotations TEXT NOT NULL DEFAULT '{}',
  icons_json TEXT NOT NULL DEFAULT '[]',
  schema_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'deprecated')),
  risk_level TEXT NOT NULL DEFAULT 'unknown',
  metadata_scan TEXT NOT NULL DEFAULT '{}',
  first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  cached_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  disabled_at TIMESTAMP,
  UNIQUE(service_id, original_name)
);
CREATE INDEX IF NOT EXISTS idx_service_tools_service ON service_tools(service_id);
CREATE INDEX IF NOT EXISTS idx_service_tools_hash ON service_tools(schema_hash);

CREATE TABLE IF NOT EXISTS tool_aliases (
  id TEXT PRIMARY KEY,
  service_tool_id TEXT NOT NULL REFERENCES service_tools(id) ON DELETE CASCADE,
  exposed_name TEXT NOT NULL,
  is_primary INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deprecated_at TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tool_aliases_exposed_name_active ON tool_aliases(exposed_name) WHERE deprecated_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tool_aliases_tool ON tool_aliases(service_tool_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_primary_alias_per_tool ON tool_aliases(service_tool_id) WHERE is_primary = 1 AND deprecated_at IS NULL;

CREATE TABLE IF NOT EXISTS service_validation_runs (
  id TEXT PRIMARY KEY,
  service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  triggered_by TEXT NOT NULL DEFAULT 'user' CHECK (triggered_by IN ('user', 'system_ttl', 'system_event', 'manual_refresh')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'success', 'failed')),
  stages TEXT NOT NULL DEFAULT '[]',
  tools_found INTEGER NOT NULL DEFAULT 0,
  errors TEXT NOT NULL DEFAULT '[]',
  warnings TEXT NOT NULL DEFAULT '[]',
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_val_runs_service ON service_validation_runs(service_id, created_at DESC);

CREATE TABLE IF NOT EXISTS toolbox_items (
  id TEXT PRIMARY KEY,
  toolbox_id TEXT NOT NULL REFERENCES toolboxes(id) ON DELETE CASCADE,
  service_id TEXT NOT NULL REFERENCES mcp_services(id),
  enabled INTEGER NOT NULL DEFAULT 1,
  added_by_user_id TEXT NOT NULL REFERENCES users(id),
  position INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_toolbox_items_active ON toolbox_items(toolbox_id, service_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tbi_toolbox ON toolbox_items(toolbox_id);

CREATE TABLE IF NOT EXISTS toolbox_tool_overrides (
  id TEXT PRIMARY KEY,
  toolbox_id TEXT NOT NULL REFERENCES toolboxes(id) ON DELETE CASCADE,
  service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  service_tool_id TEXT NOT NULL REFERENCES service_tools(id) ON DELETE CASCADE,
  enabled INTEGER NOT NULL DEFAULT 1,
  permission_level TEXT NOT NULL DEFAULT 'callable' CHECK (permission_level IN ('hidden', 'visible_only', 'callable')),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(toolbox_id, service_tool_id)
);
CREATE INDEX IF NOT EXISTS idx_tto_toolbox_service ON toolbox_tool_overrides(toolbox_id, service_id);
CREATE INDEX IF NOT EXISTS idx_tto_tool ON toolbox_tool_overrides(service_tool_id);

CREATE TABLE IF NOT EXISTS service_credentials (
  id TEXT PRIMARY KEY,
  service_id TEXT NOT NULL REFERENCES mcp_services(id) ON DELETE CASCADE,
  owner_user_id TEXT NOT NULL REFERENCES users(id),
  credential_type TEXT NOT NULL CHECK (credential_type IN ('none', 'bearer_token', 'api_key_header', 'oauth_delegated', 'service_account')),
  secret_ref TEXT NOT NULL,
  header_name TEXT,
  masked_value TEXT,
  scopes TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'connected' CHECK (status IN ('not_connected', 'connected', 'expired', 'revoked', 'error')),
  last_error_code TEXT,
  last_error_message TEXT,
  expires_at TIMESTAMP,
  rotated_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_service_credentials_service_active ON service_credentials(service_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS external_connections (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  toolbox_id TEXT REFERENCES toolboxes(id),
  client_type TEXT NOT NULL CHECK (client_type IN ('codex_cli', 'claude_code', 'claude', 'claude_desktop', 'chatgpt', 'openclaw', 'cursor', 'windsurf', 'other')),
  client_name TEXT,
  oauth_client_id TEXT,
  protocol_version TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  scopes TEXT NOT NULL DEFAULT '[]',
  client_quirks TEXT NOT NULL DEFAULT '{}',
  created_ip TEXT,
  created_user_agent TEXT,
  last_used_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ext_user ON external_connections(user_id);
CREATE INDEX IF NOT EXISTS idx_ext_client ON external_connections(client_type, status);

CREATE TABLE IF NOT EXISTS connection_tokens (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  toolbox_id TEXT REFERENCES toolboxes(id),
  token_hash TEXT NOT NULL UNIQUE,
  client_type TEXT NOT NULL,
  requested_scopes TEXT NOT NULL DEFAULT '[]',
  created_ip TEXT,
  created_user_agent TEXT,
  used_ip TEXT,
  used_user_agent TEXT,
  expires_at TIMESTAMP NOT NULL,
  used_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_otk_expires ON connection_tokens(expires_at);

CREATE TABLE IF NOT EXISTS personal_access_tokens (
  id TEXT PRIMARY KEY,
  external_connection_id TEXT REFERENCES external_connections(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id),
  token_hash TEXT NOT NULL,
  token_prefix TEXT NOT NULL,
  scopes TEXT NOT NULL DEFAULT '[]',
  protocol_version TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  last_used_at TIMESTAMP,
  expires_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT chk_pat_revoked_consistency CHECK (
    (status = 'revoked' AND revoked_at IS NOT NULL) OR
    (status = 'active' AND revoked_at IS NULL)
  )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pat_hash_active ON personal_access_tokens(token_hash) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pat_external_conn ON personal_access_tokens(external_connection_id);
CREATE INDEX IF NOT EXISTS idx_pat_user ON personal_access_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_pat_status_revoked ON personal_access_tokens(status, revoked_at);
CREATE INDEX IF NOT EXISTS idx_pat_expires ON personal_access_tokens(expires_at) WHERE expires_at IS NOT NULL AND revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS mcp_sessions (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL REFERENCES users(id),
  external_connection_id TEXT REFERENCES external_connections(id),
  client_name TEXT,
  client_version TEXT,
  protocol_version TEXT,
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  initialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP,
  terminated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sess_user ON mcp_sessions(user_id);

CREATE TABLE IF NOT EXISTS tool_invocations (
  id TEXT PRIMARY KEY,
  request_id TEXT,
  user_id TEXT NOT NULL REFERENCES users(id),
  external_connection_id TEXT REFERENCES external_connections(id),
  toolbox_id TEXT REFERENCES toolboxes(id),
  service_id TEXT REFERENCES mcp_services(id),
  service_tool_id TEXT REFERENCES service_tools(id),
  session_id TEXT,
  method TEXT,
  tool_name TEXT,
  exposed_tool_name TEXT,
  downstream_tool_name TEXT,
  status TEXT NOT NULL CHECK (status IN ('success', 'error', 'timeout', 'cancelled', 'policy_denied', 'auth_failed', 'rate_limited')),
  latency_ms INTEGER,
  downstream_latency_ms INTEGER,
  error_code TEXT,
  error_message TEXT,
  input_size_bytes INTEGER,
  output_size_bytes INTEGER,
  protocol_version TEXT,
  idempotency_key TEXT,
  client_ip TEXT,
  user_agent TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_inv_user_created ON tool_invocations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inv_service_created ON tool_invocations(service_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inv_request ON tool_invocations(request_id);
CREATE INDEX IF NOT EXISTS idx_inv_idempotency ON tool_invocations(idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS audit_logs (
  id TEXT PRIMARY KEY,
  request_id TEXT,
  actor_user_id TEXT REFERENCES users(id),
  action TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id TEXT,
  ip TEXT,
  user_agent TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aud_actor_created ON audit_logs(actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_aud_resource ON audit_logs(resource_type, resource_id);

CREATE TABLE IF NOT EXISTS debug_traces (
  id TEXT PRIMARY KEY,
  invocation_id TEXT NOT NULL REFERENCES tool_invocations(id) ON DELETE CASCADE,
  arguments_json TEXT,
  result_json TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dt_expires ON debug_traces(expires_at);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('service_validation', 'service_refresh', 'credential_rotate', 'export', 'cleanup')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'success', 'failed', 'cancelled')),
  progress REAL NOT NULL DEFAULT 0.0,
  payload TEXT NOT NULL DEFAULT '{}',
  result TEXT,
  error TEXT,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""
