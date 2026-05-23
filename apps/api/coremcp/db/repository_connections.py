from __future__ import annotations

from typing import TYPE_CHECKING, Any

from coremcp.db.repository_constants import DEFAULT_TOOLBOX_ID, LOCAL_USER_ID
from coremcp.db.repository_ids import new_id

if TYPE_CHECKING:
    from collections.abc import Iterable

    import aiosqlite


class ConnectionsRepositoryMixin:
    """External AI client connection SQL operations.

    ADR-046 Step 3 (2026-05-23): host attributes + cross-mixin methods declared
    via ``if TYPE_CHECKING:``.
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

