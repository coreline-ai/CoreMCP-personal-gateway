from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    app_name: str = "CoreMCP"
    app_version: str = "0.1.0"
    admin_token_file: Path = Field(
        default=Path("~/.coremcp/admin-token"), alias="COREMCP_ADMIN_TOKEN_FILE"
    )
    admin_token_value: str | None = Field(default=None, alias="COREMCP_ADMIN_TOKEN_VALUE")
    fake_mcp_url: str = Field(default="http://127.0.0.1:8790/mcp", alias="FAKE_MCP_URL")
    database_path: Path = Field(
        default=Path("~/.coremcp/data/coremcp.sqlite3"), alias="COREMCP_DB_PATH"
    )
    downstream_timeout_seconds: float = Field(default=35.0, alias="COREMCP_DOWNSTREAM_TIMEOUT_SECONDS")
    downstream_connect_timeout_seconds: float = Field(
        default=3.0, alias="COREMCP_DOWNSTREAM_CONNECT_TIMEOUT_SECONDS"
    )
    downstream_read_timeout_seconds: float = Field(
        default=30.0, alias="COREMCP_DOWNSTREAM_READ_TIMEOUT_SECONDS"
    )
    auth_mode: str = Field(default="static_bearer", alias="AUTH_MODE")
    expose_resource_metadata_in_static_mode: bool = Field(
        default=False, alias="EXPOSE_RESOURCE_METADATA_IN_STATIC_MODE"
    )
    metrics_enabled: bool = Field(default=False, alias="METRICS_ENABLED")
    ssrf_allow_hosts: str = Field(default="", alias="COREMCP_SSRF_ALLOW_HOSTS")
    ssrf_allow_cidrs: str = Field(default="", alias="COREMCP_SSRF_ALLOW_CIDRS")
    allow_tailscale_downstream: bool = Field(default=False, alias="ALLOW_TAILSCALE_DOWNSTREAM")
    icon_svg_enabled: bool = Field(default=False, alias="ICON_SVG_ENABLED")
    secret_backend: str = Field(default="keychain", alias="COREMCP_SECRET_BACKEND")
    secrets_file: Path = Field(default=Path("~/.coremcp/data/secrets.json"), alias="COREMCP_SECRETS_FILE")
    fernet_key_file: Path | None = Field(default=None, alias="FERNET_KEY_FILE")

    @property
    def resolved_admin_token_file(self) -> Path:
        return self.admin_token_file.expanduser()

    @property
    def resolved_database_path(self) -> Path:
        return self.database_path.expanduser()

    @property
    def resolved_secrets_file(self) -> Path:
        return self.secrets_file.expanduser()

    @property
    def resolved_fernet_key_file(self) -> Path:
        if self.fernet_key_file is not None:
            return self.fernet_key_file.expanduser()
        return self.resolved_secrets_file.with_suffix(".key")

    @property
    def ssrf_allow_host_set(self) -> set[str]:
        return {host.strip().lower() for host in self.ssrf_allow_hosts.split(",") if host.strip()}

    @property
    def ssrf_allow_cidr_list(self) -> list[str]:
        return [cidr.strip() for cidr in self.ssrf_allow_cidrs.split(",") if cidr.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
