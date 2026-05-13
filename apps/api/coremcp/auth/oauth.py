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
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from coremcp.db import Repository, new_id
from coremcp.proxy import UrlSafetyChecker, UrlSafetyError
from coremcp.settings import Settings

SUPPORTED_SCOPES = {"mcp:tools.read", "mcp:tools.call", "mcp:connections.manage"}
ACCESS_TOKEN_TTL_SECONDS = 3600
AUTHORIZATION_CODE_TTL_SECONDS = 600
CIMD_CACHE_TTL_SECONDS = 3600


class OAuthError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


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


@dataclass(slots=True)
class AuthorizationCode:
    code: str
    client_id: str
    redirect_uri: str
    resource: str
    scope: str
    code_challenge: str
    expires_at: float
    used: bool = False


@dataclass(slots=True)
class RefreshTokenRecord:
    token: str
    client_id: str
    external_connection_id: str
    resource: str
    scope: str
    expires_at: float
    family_id: str
    parent: str | None = None
    issued_at: float = 0.0
    used_at: float | None = None
    revoked: bool = False
    revoked_reason: str | None = None


class OAuthService:
    """Minimal OAuth 2.1 authorization server for personal CoreMCP mode."""

    def __init__(self, settings: Settings, repository: Repository, http_client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.repository = repository
        self.http_client = http_client
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = secrets.token_urlsafe(8)
        self.clients: dict[str, OAuthClient] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.refresh_tokens: dict[str, RefreshTokenRecord] = {}
        self.revoked_jti: dict[str, float] = {}
        self.cimd_cache: dict[str, tuple[float, OAuthClient]] = {}

    def jwks(self) -> dict[str, Any]:
        public_jwk = json.loads(RSAAlgorithm.to_jwk(self._private_key.public_key()))
        public_jwk.update({"use": "sig", "kid": self.kid, "alg": "RS256"})
        return {"keys": [public_jwk]}

    def register_client(self, metadata: dict[str, Any]) -> OAuthClient:
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
        client_id = f"cmcp_oauth_client_{secrets.token_urlsafe(24)}"
        client = OAuthClient(
            client_id=client_id,
            client_name=str(metadata.get("client_name") or "OAuth Client"),
            redirect_uris=redirect_uris,
            scope=scope,
            grant_types=[str(item) for item in grant_types],
            response_types=[str(item) for item in response_types],
            source="dcr",
        )
        self.clients[client_id] = client
        return client

    async def resolve_client(self, client_id: str) -> OAuthClient:
        if client_id in self.clients:
            return self.clients[client_id]
        if client_id.startswith("https://"):
            return await self._resolve_cimd_client(client_id)
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
    ) -> str:
        client = await self.resolve_client(client_id)
        if redirect_uri not in client.redirect_uris:
            raise OAuthError("invalid_request", "redirect_uri is not registered")
        if code_challenge_method != "S256":
            raise OAuthError("invalid_request", "PKCE S256 is required")
        if not (43 <= len(code_challenge) <= 128):
            raise OAuthError("invalid_request", "code_challenge length is invalid")
        normalized_scope = self._normalize_scope(scope or client.scope, allowed=client.scopes)
        code = f"cmcp_code_{secrets.token_urlsafe(32)}"
        self.codes[code] = AuthorizationCode(
            code=code,
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
    ) -> dict[str, Any]:
        record = self.codes.get(code)
        if record is None or record.used or record.expires_at < time.time():
            raise OAuthError("invalid_grant", "authorization code is invalid or expired")
        if record.client_id != client_id or record.redirect_uri != redirect_uri or record.resource != resource:
            raise OAuthError("invalid_grant", "authorization code binding mismatch")
        if self._pkce_challenge(code_verifier) != record.code_challenge:
            raise OAuthError("invalid_grant", "PKCE verification failed")
        record.used = True
        client = await self.resolve_client(client_id)
        connection = await self.repository.create_external_connection(
            client_type="other",
            client_name=client.client_name,
            protocol_version=None,
            scopes=record.scope.split(),
        )
        return await self._issue_token_pair(
            client=client,
            external_connection_id=connection["id"],
            resource=resource,
            scope=record.scope,
            issuer=issuer,
        )

    async def refresh(self, *, refresh_token: str, client_id: str, resource: str, issuer: str) -> dict[str, Any]:
        record = self.refresh_tokens.get(refresh_token)
        if record is None:
            raise OAuthError("invalid_grant", "refresh token is invalid or expired")
        if record.used_at is not None:
            await self._revoke_refresh_family(
                record.family_id,
                reason="reuse_detected",
                triggering_token=refresh_token,
                client_id=client_id,
            )
            raise OAuthError("invalid_grant", "refresh token reuse detected")
        if record.revoked or record.expires_at < time.time():
            raise OAuthError("invalid_grant", "refresh token is invalid or expired")
        if record.client_id != client_id or record.resource != resource:
            raise OAuthError("invalid_grant", "refresh token binding mismatch")
        now = time.time()
        record.used_at = now
        record.revoked = True
        record.revoked_reason = "rotated"
        client = await self.resolve_client(client_id)
        return await self._issue_token_pair(
            client=client,
            external_connection_id=record.external_connection_id,
            resource=resource,
            scope=record.scope,
            issuer=issuer,
            family_id=record.family_id,
            parent=refresh_token,
        )

    async def revoke(self, token: str) -> None:
        if token in self.refresh_tokens:
            self.refresh_tokens[token].revoked = True
            self.refresh_tokens[token].revoked_reason = "revoked"
            await self.repository.log_audit(action="oauth.revoke", resource_type="oauth_refresh_token")
            return
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return
        jti = unverified.get("jti")
        exp = unverified.get("exp")
        if isinstance(jti, str):
            self.revoked_jti[jti] = float(exp) if isinstance(exp, int) else time.time() + ACCESS_TOKEN_TTL_SECONDS
            await self.repository.log_audit(action="oauth.revoke", resource_type="oauth_access_token", resource_id=jti)

    def verify_access_token(self, token: str | None, *, issuer: str, audience: str) -> dict[str, Any] | None:
        if not token:
            return None
        self._purge_revoked_jti()
        try:
            claims = jwt.decode(token, self._private_key.public_key(), algorithms=["RS256"], audience=audience, issuer=issuer)
        except jwt.PyJWTError:
            return None
        jti = claims.get("jti")
        if isinstance(jti, str) and jti in self.revoked_jti:
            return None
        return claims

    async def introspect(self, token: str, *, issuer: str, audience: str) -> dict[str, Any]:
        claims = self.verify_access_token(token, issuer=issuer, audience=audience)
        if claims is None:
            refresh = self.refresh_tokens.get(token)
            return {"active": bool(refresh and not refresh.revoked and refresh.expires_at > time.time())}
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
        parent: str | None = None,
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
        access_token = jwt.encode(claims, self._private_key, algorithm="RS256", headers={"kid": self.kid})
        refresh_token = f"cmcp_refresh_{secrets.token_urlsafe(32)}"
        family_id = family_id or new_id("rtfam")
        self.refresh_tokens[refresh_token] = RefreshTokenRecord(
            token=refresh_token,
            client_id=client.client_id,
            external_connection_id=external_connection_id,
            resource=resource,
            scope=scope,
            expires_at=time.time() + 90 * 24 * 3600,
            family_id=family_id,
            parent=parent,
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
        triggering_token: str,
        client_id: str,
    ) -> None:
        revoked_count = 0
        for record in self.refresh_tokens.values():
            if record.family_id != family_id:
                continue
            if not record.revoked or record.revoked_reason != reason:
                revoked_count += 1
            record.revoked = True
            record.revoked_reason = reason
        await self.repository.log_audit(
            action="oauth.refresh_token.reuse_detected",
            resource_type="oauth_refresh_token_family",
            resource_id=family_id,
            metadata={
                "client_id": client_id,
                "triggering_token_parent": self.refresh_tokens[triggering_token].parent,
                "revoked_count": revoked_count,
            },
        )

    async def _resolve_cimd_client(self, client_id: str) -> OAuthClient:
        cached = self.cimd_cache.get(client_id)
        if cached and cached[0] > time.time():
            return cached[1]
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
            source="cimd",
        )
        self.cimd_cache[client_id] = (time.time() + CIMD_CACHE_TTL_SECONDS, client)
        return client

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

    def _purge_revoked_jti(self) -> None:
        now = time.time()
        expired = [jti for jti, exp in self.revoked_jti.items() if exp <= now]
        for jti in expired:
            self.revoked_jti.pop(jti, None)
