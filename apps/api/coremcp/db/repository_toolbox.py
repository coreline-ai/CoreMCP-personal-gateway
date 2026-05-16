from __future__ import annotations

from typing import Any

from coremcp.db.repository_constants import DEFAULT_TOOLBOX_ID, LOCAL_USER_ID
from coremcp.db.repository_ids import new_id


class ToolboxRepositoryMixin:
    """도구함 item and tool override SQL operations."""

    # ------------------------------------------------------------------
    # Toolbox
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

