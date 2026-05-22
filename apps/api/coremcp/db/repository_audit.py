# pyright: reportAttributeAccessIssue=false
# Mixin classes rely on host-provided attributes (db, dumps_json, loads_json,
# and cross-mixin methods); the composing Repository class supplies them at
# runtime. Type checker cannot resolve them without a circular base class.
from __future__ import annotations

from typing import Any

from coremcp.logging import redact_value
from coremcp.db.repository_constants import DEFAULT_TOOLBOX_ID, LOCAL_USER_ID
from coremcp.db.repository_ids import new_id


class AuditRepositoryMixin:
    """User, audit, invocation, metrics, and dashboard SQL operations."""

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
                self.dumps_json(redact_value(metadata or {})),
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

