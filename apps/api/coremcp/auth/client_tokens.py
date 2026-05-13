from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any

from coremcp.db import Repository

CLIENT_TOKEN_PREFIX = "cmcp_client_"


def generate_client_token() -> str:
    return f"{CLIENT_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_prefix(token: str) -> str:
    if len(token) <= 24:
        return token
    return f"{token[:20]}…{token[-4:]}"


def mask_token(prefix: str) -> str:
    if "…" in prefix:
        return prefix
    if len(prefix) <= 12:
        return f"{prefix}••••"
    return f"{prefix[:12]}••••{prefix[-4:]}"


@dataclass(slots=True)
class ClientTokenAuth:
    token_id: str
    external_connection_id: str
    scopes: list[str]
    protocol_version: str | None = None


class ClientTokenService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    async def issue(
        self,
        *,
        external_connection_id: str,
        scopes: list[str] | None = None,
        protocol_version: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        token = generate_client_token()
        record = await self.repository.create_personal_access_token(
            external_connection_id=external_connection_id,
            token_hash=hash_token(token),
            token_prefix=token_prefix(token),
            scopes=scopes,
            protocol_version=protocol_version,
            expires_at=expires_at,
        )
        return {**record, "token": token}

    async def verify(self, presented: str | None) -> ClientTokenAuth | None:
        if not presented or not presented.startswith(CLIENT_TOKEN_PREFIX):
            return None
        record = await self.repository.find_active_personal_access_token_by_hash(hash_token(presented))
        if record is None:
            return None
        return ClientTokenAuth(
            token_id=record["id"],
            external_connection_id=record["external_connection_id"],
            scopes=record.get("scopes") or [],
            protocol_version=record.get("protocol_version"),
        )

    async def revoke(self, token_id: str) -> bool:
        return await self.repository.revoke_personal_access_token(token_id)
