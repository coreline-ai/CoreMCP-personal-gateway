from __future__ import annotations

import base64
import json
import platform
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from coremcp.errors import CoreMcpRuntimeError
from coremcp.settings import Settings


class CredentialVaultError(CoreMcpRuntimeError):
    pass


def mask_secret(secret: str) -> str:
    if not secret:
        return "••••"
    if len(secret) <= 8:
        return f"{secret[:2]}••••"
    return f"{secret[:4]}••••{secret[-4:]}"


class CredentialVault(ABC):
    @abstractmethod
    async def put(self, *, service_id: str, secret: str) -> str: ...

    @abstractmethod
    async def get(self, secret_ref: str) -> str | None: ...

    @abstractmethod
    async def delete(self, secret_ref: str) -> None: ...

    @abstractmethod
    async def is_ready(self) -> bool: ...


class FileVaultBackend(CredentialVault):
    """Legacy base64-only vault retained for old local development data.

    This backend is not encryption. New file-backed credentials should use
    FernetBackend via build_vault().
    """

    prefix = "legacy-base64"

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()

    async def put(self, *, service_id: str, secret: str) -> str:
        data = self._read()
        ref = f"{self.prefix}:coremcp:{service_id}:{uuid4().hex}"
        data[ref] = base64.urlsafe_b64encode(secret.encode("utf-8")).decode("ascii")
        self._write(data)
        return ref

    async def get(self, secret_ref: str) -> str | None:
        data = self._read()
        encoded = data.get(secret_ref)
        if not encoded:
            return None
        try:
            return base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
        except Exception as exc:  # pragma: no cover - corrupt local file path
            raise CredentialVaultError("Stored credential could not be decoded") from exc

    async def delete(self, secret_ref: str) -> None:
        data = self._read()
        data.pop(secret_ref, None)
        self._write(data)

    async def is_ready(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.exists():
                self._write({})
            return True
        except OSError:
            return False

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            value: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CredentialVaultError("Credential vault file is not valid JSON") from exc
        if not isinstance(value, dict):
            raise CredentialVaultError("Credential vault file must be an object")
        return {str(key): str(item) for key, item in value.items()}

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass


class FernetBackend(CredentialVault):
    """File-backed Fernet vault for headless/CI operation."""

    prefix = "fernet"

    def __init__(self, path: Path, key_path: Path) -> None:
        self.path = path.expanduser()
        self.key_path = key_path.expanduser()

    async def put(self, *, service_id: str, secret: str) -> str:
        data = self._read()
        ref = f"{self.prefix}:coremcp:{service_id}:{uuid4().hex}"
        ciphertext = self._fernet().encrypt(secret.encode("utf-8")).decode("ascii")
        data[ref] = {"version": 2, "ciphertext": ciphertext}
        self._write(data)
        return ref

    async def get(self, secret_ref: str) -> str | None:
        data = self._read()
        item = data.get(secret_ref)
        if item is None:
            return None
        if isinstance(item, dict):
            ciphertext = item.get("ciphertext")
            if not isinstance(ciphertext, str):
                raise CredentialVaultError("Stored credential record is malformed")
            try:
                return self._fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
            except (InvalidToken, UnicodeDecodeError) as exc:
                raise CredentialVaultError("Stored credential could not be decrypted") from exc
        if isinstance(item, str):
            return self._decode_legacy_base64(item)
        raise CredentialVaultError("Stored credential record is malformed")

    async def delete(self, secret_ref: str) -> None:
        data = self._read()
        data.pop(secret_ref, None)
        self._write(data)

    async def is_ready(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.exists():
                self._write({})
            self._fernet()
            return True
        except (CredentialVaultError, OSError, ValueError):
            return False

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            value: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CredentialVaultError("Credential vault file is not valid JSON") from exc
        if not isinstance(value, dict):
            raise CredentialVaultError("Credential vault file must be an object")
        return {str(key): item for key, item in value.items()}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _fernet(self) -> Fernet:
        key = self._load_or_create_key()
        try:
            return Fernet(key)
        except ValueError as exc:
            raise CredentialVaultError("Fernet key is invalid") from exc

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            try:
                self.key_path.chmod(0o600)
            except OSError:
                pass
            return self.key_path.read_bytes().strip()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        self.key_path.write_bytes(key + b"\n")
        try:
            self.key_path.chmod(0o600)
        except OSError:
            pass
        return key

    @staticmethod
    def _decode_legacy_base64(encoded: str) -> str:
        try:
            return base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
        except Exception as exc:  # pragma: no cover - corrupt legacy local file path
            raise CredentialVaultError("Stored legacy credential could not be decoded") from exc


class KeychainBackend(CredentialVault):
    prefix = "keychain"

    def __init__(self, fallback: CredentialVault) -> None:
        self.fallback = fallback
        self.available = platform.system() == "Darwin" and shutil.which("security") is not None

    async def put(self, *, service_id: str, secret: str) -> str:
        if not self.available:
            return await self.fallback.put(service_id=service_id, secret=secret)
        account = f"{service_id}:{uuid4().hex}"
        ref = f"{self.prefix}:coremcp:{account}"
        result = subprocess.run(
            ["security", "add-generic-password", "-a", account, "-s", "coremcp", "-U", "-w"],
            input=f"{secret}\n",
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return await self.fallback.put(service_id=service_id, secret=secret)
        return ref

    async def get(self, secret_ref: str) -> str | None:
        if not secret_ref.startswith(f"{self.prefix}:coremcp:") or not self.available:
            return await self.fallback.get(secret_ref)
        account = secret_ref.removeprefix(f"{self.prefix}:coremcp:")
        result = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", "coremcp", "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.rstrip("\n")

    async def delete(self, secret_ref: str) -> None:
        if not secret_ref.startswith(f"{self.prefix}:coremcp:") or not self.available:
            await self.fallback.delete(secret_ref)
            return
        account = secret_ref.removeprefix(f"{self.prefix}:coremcp:")
        subprocess.run(
            ["security", "delete-generic-password", "-a", account, "-s", "coremcp"],
            capture_output=True,
            text=True,
            check=False,
        )

    async def is_ready(self) -> bool:
        if self.available:
            return True
        return await self.fallback.is_ready()


def build_vault(settings: Settings) -> CredentialVault:
    fallback = FernetBackend(settings.resolved_secrets_file, settings.resolved_fernet_key_file)
    if settings.secret_backend.lower() == "keychain":
        return KeychainBackend(fallback)
    return fallback
