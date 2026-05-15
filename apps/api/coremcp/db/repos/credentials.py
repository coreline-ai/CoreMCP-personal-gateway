"""Credential, token, and OAuth repository facade."""

from __future__ import annotations

from typing import Any

from ._base import RepositoryDomainFacade


class CredentialRepository(RepositoryDomainFacade):
    """Thin facade for vault-backed credentials and auth token records."""

    def create_personal_access_token(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("create_personal_access_token", *args, **kwargs)

    def get_personal_access_token(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_personal_access_token", *args, **kwargs)

    def list_personal_access_tokens(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("list_personal_access_tokens", *args, **kwargs)

    def find_active_personal_access_token_by_hash(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("find_active_personal_access_token_by_hash", *args, **kwargs)

    def revoke_personal_access_token(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("revoke_personal_access_token", *args, **kwargs)

    def create_connection_token(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("create_connection_token", *args, **kwargs)

    def get_connection_token(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_connection_token", *args, **kwargs)

    def consume_connection_token(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("consume_connection_token", *args, **kwargs)

    def get_active_oauth_signing_key(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_active_oauth_signing_key", *args, **kwargs)

    def create_oauth_signing_key(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("create_oauth_signing_key", *args, **kwargs)

    def update_oauth_signing_key_private_material(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("update_oauth_signing_key_private_material", *args, **kwargs)

    def upsert_oauth_client(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("upsert_oauth_client", *args, **kwargs)

    def get_oauth_client(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_oauth_client", *args, **kwargs)

    def create_oauth_authorization_code(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("create_oauth_authorization_code", *args, **kwargs)

    def consume_oauth_authorization_code(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("consume_oauth_authorization_code", *args, **kwargs)

    def create_oauth_refresh_token(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("create_oauth_refresh_token", *args, **kwargs)

    def find_oauth_refresh_token_by_hash(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("find_oauth_refresh_token_by_hash", *args, **kwargs)

    def mark_oauth_refresh_token_rotated(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("mark_oauth_refresh_token_rotated", *args, **kwargs)

    def revoke_oauth_refresh_token(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("revoke_oauth_refresh_token", *args, **kwargs)

    def revoke_oauth_refresh_family(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("revoke_oauth_refresh_family", *args, **kwargs)

    def upsert_oauth_revoked_access_jti(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("upsert_oauth_revoked_access_jti", *args, **kwargs)

    def is_oauth_access_jti_revoked(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("is_oauth_access_jti_revoked", *args, **kwargs)

    def upsert_oauth_cimd_client(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("upsert_oauth_cimd_client", *args, **kwargs)

    def get_cached_oauth_cimd_client(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_cached_oauth_cimd_client", *args, **kwargs)

    def upsert_service_credential(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("upsert_service_credential", *args, **kwargs)

    def get_service_credential(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("get_service_credential", *args, **kwargs)

    def revoke_service_credential(self, *args: Any, **kwargs: Any) -> Any:
        return self._delegate("revoke_service_credential", *args, **kwargs)
