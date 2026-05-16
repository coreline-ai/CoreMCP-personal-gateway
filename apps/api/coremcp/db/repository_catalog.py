from __future__ import annotations

from typing import Any

from coremcp.db.repository_constants import DEFAULT_TOOLBOX_ID
from coremcp.db.repository_ids import new_id


class CatalogRepositoryMixin:
    """Discovered tool/resource/prompt catalog SQL operations."""

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
