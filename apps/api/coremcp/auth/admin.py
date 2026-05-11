import hmac
from pathlib import Path

from coremcp.settings import Settings


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
    except FileNotFoundError:
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
