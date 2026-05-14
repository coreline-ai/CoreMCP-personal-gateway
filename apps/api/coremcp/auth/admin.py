import hmac
import os
import secrets
import tempfile
from pathlib import Path

from coremcp.settings import Settings

ADMIN_TOKEN_PREFIX = "cmcp_admin_"


class AdminTokenFileError(RuntimeError):
    pass


def extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        return None
    return token.strip() or None


def _read_token_file(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None
    return value or None


def load_admin_token(settings: Settings) -> str | None:
    """Load the admin token with file priority over env value.

    Tests may set COREMCP_ADMIN_TOKEN_VALUE, but production defaults to
    ~/.coremcp/admin-token and always prefers that file when present.
    """

    file_token = _read_token_file(settings.resolved_admin_token_file)
    if file_token is not None:
        return file_token
    return settings.admin_token_value


def verify_admin_bearer(presented: str | None, settings: Settings) -> bool:
    expected = load_admin_token(settings)
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented, expected)


def generate_admin_token() -> str:
    return f"{ADMIN_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def write_admin_token_atomic(path: Path, token: str) -> None:
    """Persist an admin token with same-directory atomic replace and 0600 perms."""

    resolved = path.expanduser()
    if resolved.exists() and resolved.is_dir():
        raise AdminTokenFileError("admin token path points to a directory")

    parent = resolved.parent
    tmp_name: str | None = None
    fd: int | None = None
    try:
        parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{resolved.name}.", suffix=".tmp", dir=str(parent))
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            handle.write(token)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, resolved)
        tmp_name = None
        os.chmod(resolved, 0o600)
        try:
            dir_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # Directory fsync is a best-effort durability improvement and is not
            # supported on every platform/filesystem used in local development.
            pass
    except OSError as exc:
        raise AdminTokenFileError(str(exc)) from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass
