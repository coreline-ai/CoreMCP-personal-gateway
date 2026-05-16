from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from coremcp.auth.rate_limit import FixedWindowRateLimiter
from coremcp.credentials import CredentialVault
from coremcp.db import Repository, new_id
from coremcp.errors import CoreMcpValueError
from coremcp.proxy import UrlSafetyChecker, UrlSafetyError
from coremcp.settings import Settings

SUPPORTED_SCOPES = {"mcp:tools.read", "mcp:tools.call", "mcp:connections.manage"}
ACCESS_TOKEN_TTL_SECONDS = 3600
AUTHORIZATION_CODE_TTL_SECONDS = 600
CIMD_CACHE_TTL_SECONDS = 3600
CIMD_RATE_LIMIT = 30
CIMD_RATE_LIMIT_WINDOW_SECONDS = 3600
REFRESH_TOKEN_TTL_SECONDS = 90 * 24 * 3600


class OAuthError(CoreMcpValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


@dataclass(slots=True)
class OAuthClient:
    client_id: str
    client_name: str
    redirect_uris: list[str]
    scope: str = "mcp:tools.read mcp:tools.call"
    grant_types: list[str] | None = None
    response_types: list[str] | None = None
    token_endpoint_auth_method: str = "none"
    source: str = "dcr"

    @property
    def scopes(self) -> set[str]:
        return set(self.scope.split()) if self.scope else set()


class OAuthService:
    """Minimal OAuth 2.1 authorization server for personal CoreMCP mode.

    OAuth tokens and server state are persisted in SQLite so launchd/API restarts
    do not invalidate active clients. Raw authorization codes and refresh tokens
    are never stored; only SHA-256 hashes are persisted.
    """

    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        http_client: httpx.AsyncClient,
        *,
        cimd_rate_limiter: FixedWindowRateLimiter | None = None,
        vault: CredentialVault | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.http_client = http_client
        self.cimd_rate_limiter = cimd_rate_limiter or FixedWindowRateLimiter()
        self.vault = vault
        self._private_key: rsa.RSAPrivateKey | None = None
        self.kid: str | None = None

    async def startup(self) -> None:
        key = await self.repository.get_active_oauth_signing_key()
        if key is None:
            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode("ascii")
            kid = secrets.token_urlsafe(8)
            private_material_ref = await self._store_private_key_pem(kid=kid, pem=pem)
            await self.repository.create_oauth_signing_key(kid=kid, private_key_pem=private_material_ref, alg="RS256")
            self._private_key = private_key
            self.kid = kid
            return
        kid = str(key["kid"])
        private_material = str(key["private_key_pem"])
        pem = await self._load_private_key_pem(private_material)
        if private_material == pem and self.vault is not None:
            # Legacy rows may contain plaintext PEM from early local builds.
            # Migrate in-place to a vault reference without rotating the key.
            migrated_ref = await self._store_private_key_pem(kid=kid, pem=pem)
            await self.repository.update_oauth_signing_key_private_material(kid=kid, private_key_pem=migrated_ref)
        loaded_key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
        if not isinstance(loaded_key, rsa.RSAPrivateKey):
            raise RuntimeError("OAuth signing key must be an RSA private key")
        self._private_key = loaded_key
        self.kid = kid

    async def shutdown(self) -> None:
        """Drop in-memory signing material on API shutdown.

        Persisted OAuth keys and token records remain in SQLite/vault. This
        method only clears process-local cryptographic state so restart and
        test lifecycles have an explicit teardown contract.
        """

        self._private_key = None
        self.kid = None

    async def _store_private_key_pem(self, *, kid: str, pem: str) -> str:
        if self.vault is None:
            return pem
        return await self.vault.put(service_id=f"oauth-signing-key:{kid}", secret=pem)

    async def _load_private_key_pem(self, private_material: str) -> str:
        if private_material.startswith("-----BEGIN"):
            return private_material
        if self.vault is None:
            raise RuntimeError("OAuth signing key is stored as a vault reference but no vault is configured")
        pem = await self.vault.get(private_material)
        if not pem:
            raise RuntimeError("OAuth signing key could not be loaded from the credential vault")
        return pem

    @property
    def private_key(self) -> rsa.RSAPrivateKey:
        if self._private_key is None or self.kid is None:
            raise RuntimeError("OAuthService.startup() has not loaded a signing key")
        return self._private_key

    def jwks(self) -> dict[str, Any]:
        public_jwk = json.loads(RSAAlgorithm.to_jwk(self.private_key.public_key()))
        public_jwk.update({"use": "sig", "kid": self.kid, "alg": "RS256"})
        return {"keys": [public_jwk]}

    async def register_client(self, metadata: dict[str, Any]) -> OAuthClient:
        redirect_uris = metadata.get("redirect_uris")
        if not isinstance(redirect_uris, list) or not redirect_uris or not all(isinstance(uri, str) for uri in redirect_uris):
            raise OAuthError("invalid_client_metadata", "redirect_uris is required")
        for redirect_uri in redirect_uris:
            self._validate_redirect_uri(redirect_uri)
        grant_types = metadata.get("grant_types") if isinstance(metadata.get("grant_types"), list) else ["authorization_code", "refresh_token"]
        response_types = metadata.get("response_types") if isinstance(metadata.get("response_types"), list) else ["code"]
        if "authorization_code" not in grant_types or "code" not in response_types:
            raise OAuthError("invalid_client_metadata", "authorization_code/code support is required")
        method = metadata.get("token_endpoint_auth_method") or "none"
        if method != "none":
            raise OAuthError("invalid_client_metadata", "only public clients with token_endpoint_auth_method=none are supported")
        scope = self._normalize_scope(str(metadata.get("scope") or "mcp:tools.read mcp:tools.call"))
        client = OAuthClient(
            client_id=f"cmcp_oauth_client_{secrets.token_urlsafe(24)}",
            client_name=str(metadata.get("client_name") or "OAuth Client"),
            redirect_uris=redirect_uris,
            scope=scope,
            grant_types=[str(item) for item in grant_types],
            response_types=[str(item) for item in response_types],
            token_endpoint_auth_method="none",
            source="dcr",
        )
        await self.repository.upsert_oauth_client(
            client_id=client.client_id,
            client_name=client.client_name,
            redirect_uris=client.redirect_uris,
            scope=client.scope,
            grant_types=client.grant_types or ["authorization_code", "refresh_token"],
            response_types=client.response_types or ["code"],
            token_endpoint_auth_method=client.token_endpoint_auth_method,
            source=client.source,
        )
        return client

    async def resolve_client(self, client_id: str, *, client_ip: str | None = None) -> OAuthClient:
        row = await self.repository.get_oauth_client(client_id)
        if row is not None:
            return self._client_from_row(row)
        if client_id.startswith("https://"):
            return await self._resolve_cimd_client(client_id, client_ip=client_ip)
        raise OAuthError("invalid_client", "client is not registered", status_code=401)

    async def create_authorization_code(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        resource: str,
        scope: str,
        code_challenge: str,
        code_challenge_method: str,
        client_ip: str | None = None,
    ) -> str:
        client = await self.resolve_client(client_id, client_ip=client_ip)
        if redirect_uri not in client.redirect_uris:
            raise OAuthError("invalid_request", "redirect_uri is not registered")
        if code_challenge_method != "S256":
            raise OAuthError("invalid_request", "PKCE S256 is required")
        if not (43 <= len(code_challenge) <= 128):
            raise OAuthError("invalid_request", "code_challenge length is invalid")
        normalized_scope = self._normalize_scope(scope or client.scope, allowed=client.scopes)
        code = f"cmcp_code_{secrets.token_urlsafe(32)}"
        await self.repository.create_oauth_authorization_code(
            code_hash=self._hash_token(code),
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            resource=resource,
            scope=normalized_scope,
            code_challenge=code_challenge,
            expires_at=time.time() + AUTHORIZATION_CODE_TTL_SECONDS,
        )
        await self.repository.log_audit(
            action="oauth.authorize",
            resource_type="oauth_client",
            resource_id=client.client_id,
            metadata={"scope": normalized_scope, "resource": resource},
        )
        return code

    async def exchange_authorization_code(
        self,
        *,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
        resource: str,
        issuer: str,
        client_ip: str | None = None,
    ) -> dict[str, Any]:
        record = await self.repository.consume_oauth_authorization_code(
            code_hash=self._hash_token(code),
            client_id=client_id,
            redirect_uri=redirect_uri,
            resource=resource,
            now=time.time(),
        )
        if record is None:
            raise OAuthError("invalid_grant", "authorization code is invalid or expired")
        if self._pkce_challenge(code_verifier) != record["code_challenge"]:
            raise OAuthError("invalid_grant", "PKCE verification failed")
        client = await self.resolve_client(client_id, client_ip=client_ip)
        connection = await self.repository.create_external_connection(
            client_type="other",
            client_name=client.client_name,
            oauth_client_id=client.client_id,
            protocol_version=None,
            scopes=record["scope"].split(),
        )
        return await self._issue_token_pair(
            client=client,
            external_connection_id=connection["id"],
            resource=resource,
            scope=record["scope"],
            issuer=issuer,
        )

    async def refresh(
        self,
        *,
        refresh_token: str,
        client_id: str,
        resource: str,
        issuer: str,
        client_ip: str | None = None,
    ) -> dict[str, Any]:
        token_hash = self._hash_token(refresh_token)
        record = await self.repository.find_oauth_refresh_token_by_hash(token_hash)
        if record is None:
            raise OAuthError("invalid_grant", "refresh token is invalid or expired")
        if record.get("used_at") is not None:
            await self._revoke_refresh_family(
                str(record["family_id"]),
                reason="reuse_detected",
                triggering_token_hash=token_hash,
                triggering_parent_hash=record.get("parent_hash"),
                client_id=client_id,
            )
            raise OAuthError("invalid_grant", "refresh token reuse detected")
        if record.get("revoked_at") is not None or float(record["expires_at"]) < time.time():
            raise OAuthError("invalid_grant", "refresh token is invalid or expired")
        if record["client_id"] != client_id or record["resource"] != resource:
            raise OAuthError("invalid_grant", "refresh token binding mismatch")
        now = time.time()
        if not await self.repository.mark_oauth_refresh_token_rotated(token_hash=token_hash, now=now):
            latest = await self.repository.find_oauth_refresh_token_by_hash(token_hash)
            if latest and latest.get("used_at") is not None:
                await self._revoke_refresh_family(
                    str(latest["family_id"]),
                    reason="reuse_detected",
                    triggering_token_hash=token_hash,
                    triggering_parent_hash=latest.get("parent_hash"),
                    client_id=client_id,
                )
            raise OAuthError("invalid_grant", "refresh token is invalid or expired")
        client = await self.resolve_client(client_id, client_ip=client_ip)
        return await self._issue_token_pair(
            client=client,
            external_connection_id=str(record["external_connection_id"]),
            resource=resource,
            scope=str(record["scope"]),
            issuer=issuer,
            family_id=str(record["family_id"]),
            parent_hash=token_hash,
        )

    async def revoke(self, token: str) -> None:
        token_hash = self._hash_token(token)
        if await self.repository.revoke_oauth_refresh_token(token_hash=token_hash, reason="revoked", now=time.time()):
            await self.repository.log_audit(action="oauth.revoke", resource_type="oauth_refresh_token")
            return
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return
        jti = unverified.get("jti")
        exp = unverified.get("exp")
        if isinstance(jti, str):
            await self.repository.upsert_oauth_revoked_access_jti(
                jti=jti,
                expires_at=float(exp) if isinstance(exp, int) else time.time() + ACCESS_TOKEN_TTL_SECONDS,
                now=time.time(),
            )
            await self.repository.log_audit(action="oauth.revoke", resource_type="oauth_access_token", resource_id=jti)

    async def verify_access_token(self, token: str | None, *, issuer: str, audience: str) -> dict[str, Any] | None:
        if not token:
            return None
        try:
            claims = jwt.decode(token, self.private_key.public_key(), algorithms=["RS256"], audience=audience, issuer=issuer)
        except jwt.PyJWTError:
            return None
        jti = claims.get("jti")
        if isinstance(jti, str) and await self.repository.is_oauth_access_jti_revoked(jti=jti, now=time.time()):
            return None
        return claims

    async def introspect(self, token: str, *, issuer: str, audience: str) -> dict[str, Any]:
        claims = await self.verify_access_token(token, issuer=issuer, audience=audience)
        if claims is None:
            refresh = await self.repository.find_oauth_refresh_token_by_hash(self._hash_token(token))
            return {"active": bool(refresh and refresh.get("revoked_at") is None and float(refresh["expires_at"]) > time.time())}
        return {"active": True, **claims}

    async def _issue_token_pair(
        self,
        *,
        client: OAuthClient,
        external_connection_id: str,
        resource: str,
        scope: str,
        issuer: str,
        family_id: str | None = None,
        parent_hash: str | None = None,
    ) -> dict[str, Any]:
        now = int(time.time())
        jti = new_id("jti")
        claims = {
            "iss": issuer,
            "sub": "usr_local",
            "aud": resource,
            "iat": now,
            "exp": now + ACCESS_TOKEN_TTL_SECONDS,
            "jti": jti,
            "scope": scope,
            "client_id": client.client_id,
            "external_connection_id": external_connection_id,
        }
        access_token = jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": self.kid})
        refresh_token = f"cmcp_refresh_{secrets.token_urlsafe(32)}"
        family_id = family_id or new_id("rtfam")
        await self.repository.create_oauth_refresh_token(
            token_hash=self._hash_token(refresh_token),
            client_id=client.client_id,
            external_connection_id=external_connection_id,
            resource=resource,
            scope=scope,
            expires_at=time.time() + REFRESH_TOKEN_TTL_SECONDS,
            family_id=family_id,
            parent_hash=parent_hash,
            issued_at=time.time(),
        )
        await self.repository.log_audit(
            action="oauth.token.issue",
            resource_type="external_connection",
            resource_id=external_connection_id,
            metadata={"client_id": client.client_id, "scope": scope, "refresh_family_id": family_id},
        )
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_TTL_SECONDS,
            "refresh_token": refresh_token,
            "scope": scope,
        }

    async def _revoke_refresh_family(
        self,
        family_id: str,
        *,
        reason: str,
        triggering_token_hash: str,
        triggering_parent_hash: str | None,
        client_id: str,
    ) -> None:
        revoked_count = await self.repository.revoke_oauth_refresh_family(family_id=family_id, reason=reason, now=time.time())
        await self.repository.log_audit(
            action="oauth.refresh_token.reuse_detected",
            resource_type="oauth_refresh_token_family",
            resource_id=family_id,
            metadata={
                "client_id": client_id,
                "triggering_token_hash_prefix": triggering_token_hash[:12],
                "triggering_token_parent_hash_prefix": triggering_parent_hash[:12] if triggering_parent_hash else None,
                "revoked_count": revoked_count,
            },
        )

    async def _resolve_cimd_client(self, client_id: str, *, client_ip: str | None = None) -> OAuthClient:
        decision = self.cimd_rate_limiter.check(
            f"oauth:cimd:{client_ip or 'unknown'}",
            limit=CIMD_RATE_LIMIT,
            window_seconds=CIMD_RATE_LIMIT_WINDOW_SECONDS,
        )
        if not decision.allowed:
            raise OAuthError(
                "rate_limited",
                "CIMD client_id metadata resolution rate limit exceeded",
                status_code=429,
                retry_after_seconds=decision.retry_after_seconds,
            )
        cached = await self.repository.get_cached_oauth_cimd_client(client_id=client_id, now=time.time())
        if cached is not None:
            return self._client_from_row(cached)
        checker = UrlSafetyChecker(self.settings)
        try:
            checker.assert_safe(client_id)
        except UrlSafetyError as exc:
            raise OAuthError("unsafe_client_id", str(exc)) from exc
        try:
            response = await self.http_client.get(client_id, headers={"Accept": "application/json"}, follow_redirects=False, timeout=5.0)
        except httpx.HTTPError as exc:
            raise OAuthError("invalid_client_metadata", f"CIMD fetch failed: {exc}") from exc
        if 300 <= response.status_code < 400:
            raise OAuthError("invalid_client_metadata", "CIMD redirects are not allowed")
        if response.status_code >= 500:
            raise OAuthError("cimd_unavailable", "CIMD endpoint is unavailable", status_code=503)
        if response.status_code >= 400:
            raise OAuthError("invalid_client_metadata", "CIMD endpoint rejected request")
        content_type = response.headers.get("content-type", "").lower()
        media_type, _, _parameters = content_type.partition(";")
        if media_type.strip() != "application/json":
            raise OAuthError("invalid_client_metadata", "CIMD content-type must be application/json")
        if len(response.content) > 32 * 1024:
            raise OAuthError("invalid_client_metadata", "CIMD response exceeds 32KB")
        try:
            metadata = response.json()
        except ValueError as exc:
            raise OAuthError("invalid_client_metadata", "CIMD response is not valid JSON") from exc
        if not isinstance(metadata, dict) or metadata.get("client_id") != client_id:
            raise OAuthError("client_id_mismatch", "CIMD client_id must match byte-for-byte")
        redirect_uris = metadata.get("redirect_uris")
        if not isinstance(redirect_uris, list) or not all(isinstance(uri, str) for uri in redirect_uris):
            raise OAuthError("invalid_client_metadata", "CIMD redirect_uris is required")
        client_host = urlparse(client_id).hostname
        for redirect_uri in redirect_uris:
            self._validate_redirect_uri(redirect_uri)
            if urlparse(redirect_uri).hostname != client_host:
                raise OAuthError("invalid_client_metadata", "CIMD redirect host must match client_id host")
        client = OAuthClient(
            client_id=client_id,
            client_name=str(metadata.get("client_name") or client_id),
            redirect_uris=redirect_uris,
            scope=self._normalize_scope(str(metadata.get("scope") or "mcp:tools.read mcp:tools.call")),
            grant_types=[str(item) for item in metadata.get("grant_types", ["authorization_code", "refresh_token"])],
            response_types=[str(item) for item in metadata.get("response_types", ["code"])],
            token_endpoint_auth_method="none",
            source="cimd",
        )
        await self.repository.upsert_oauth_cimd_client(
            client_id=client.client_id,
            client_name=client.client_name,
            redirect_uris=client.redirect_uris,
            scope=client.scope,
            grant_types=client.grant_types or ["authorization_code", "refresh_token"],
            response_types=client.response_types or ["code"],
            token_endpoint_auth_method=client.token_endpoint_auth_method,
            cached_until=time.time() + CIMD_CACHE_TTL_SECONDS,
        )
        return client

    @staticmethod
    def _client_from_row(row: dict[str, Any]) -> OAuthClient:
        return OAuthClient(
            client_id=str(row["client_id"]),
            client_name=str(row.get("client_name") or "OAuth Client"),
            redirect_uris=[str(item) for item in row.get("redirect_uris") or []],
            scope=str(row.get("scope") or "mcp:tools.read mcp:tools.call"),
            grant_types=[str(item) for item in row.get("grant_types") or ["authorization_code", "refresh_token"]],
            response_types=[str(item) for item in row.get("response_types") or ["code"]],
            token_endpoint_auth_method=str(row.get("token_endpoint_auth_method") or "none"),
            source=str(row.get("source") or "dcr"),
        )

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _pkce_challenge(code_verifier: str) -> str:
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def redirect_with_code(redirect_uri: str, *, code: str, state: str | None = None) -> str:
        params = {"code": code}
        if state:
            params["state"] = state
        separator = "&" if "?" in redirect_uri else "?"
        return f"{redirect_uri}{separator}{urlencode(params)}"

    @staticmethod
    def _validate_redirect_uri(redirect_uri: str) -> None:
        parsed = urlparse(redirect_uri)
        if parsed.scheme == "https" and parsed.hostname:
            return
        if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            return
        raise OAuthError("invalid_redirect_uri", "redirect_uri must be https or localhost http")

    @staticmethod
    def _normalize_scope(scope: str, allowed: set[str] | None = None) -> str:
        requested = set(scope.split()) if scope else {"mcp:tools.read", "mcp:tools.call"}
        allowed = allowed or SUPPORTED_SCOPES
        if not requested.issubset(allowed) or not requested.issubset(SUPPORTED_SCOPES):
            raise OAuthError("invalid_scope", "requested scope is not supported")
        return " ".join(sorted(requested))
