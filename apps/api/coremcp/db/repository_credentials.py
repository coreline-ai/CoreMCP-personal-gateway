from __future__ import annotations

from typing import TYPE_CHECKING, Any

from coremcp.db.repository_constants import DEFAULT_TOOLBOX_ID, LOCAL_USER_ID
from coremcp.db.repository_ids import new_id

if TYPE_CHECKING:
    from collections.abc import Iterable

    import aiosqlite


class CredentialsRepositoryMixin:
    """Credential, client token, one-time token, and OAuth persistence SQL operations.

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
        async def get_external_connection(self, connection_id: str) -> dict[str, Any] | None: ...

    # ------------------------------------------------------------------
    # Client tokens / one-time connection tokens
    # ------------------------------------------------------------------
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
