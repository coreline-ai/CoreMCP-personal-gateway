from __future__ import annotations

from typing import TYPE_CHECKING, Any

from coremcp.db.repository_constants import LOCAL_USER_ID
from coremcp.db.repository_ids import new_id

if TYPE_CHECKING:
    from collections.abc import Iterable

    import aiosqlite


class ServicesRepositoryMixin:
    """MCP service registry SQL operations.

    ADR-046 Step 3 (2026-05-23): host-provided attributes + cross-mixin methods
    declared via ``if TYPE_CHECKING:`` so this module no longer needs the
    ``reportAttributeAccessIssue=false`` directive.
    """

    if TYPE_CHECKING:
        @property
        def db(self) -> aiosqlite.Connection: ...

        @staticmethod
        def dumps_json(value: Any) -> str: ...

        @staticmethod
        def dumps_json_array(value: Any) -> str: ...

        @staticmethod
        def loads_json(value: Any, default: Any = None) -> Any: ...

        @staticmethod
        def _row_to_dict(
            row: aiosqlite.Row | None, json_fields: Iterable[str] = ()
        ) -> dict[str, Any] | None: ...

        async def log_audit(self, **kwargs: Any) -> str: ...

    # ------------------------------------------------------------------
    # MCP services
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
            # Probe success also clears stale 'error'/'auth_required' status so
            # services recover automatically once the downstream becomes healthy
            # again. Admin-imposed states ('disabled', 'draft', 'validating',
            # 'deleted') are left untouched.
            await self.db.execute(
                """
                UPDATE mcp_services
                SET last_health_check_at = CURRENT_TIMESTAMP,
                    consecutive_failures = 0,
                    circuit_open_until = NULL,
                    status = CASE
                        WHEN status IN ('error', 'auth_required') THEN 'active'
                        ELSE status
                    END,
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

