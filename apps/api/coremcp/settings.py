from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_CORS_ALLOWED_ORIGINS = (
    "http://localhost:3000,"
    "http://localhost:3003,"
    "http://127.0.0.1:3000,"
    "http://127.0.0.1:3003"
)
DEFAULT_STDIO_ALLOWED_COMMANDS = "npx,uvx,python,python3,node,docker,deno"
DEFAULT_ALLOWED_HOSTS = "localhost,127.0.0.1,::1,testserver"


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
    service_health_probe_enabled: bool = Field(default=True, alias="COREMCP_SERVICE_HEALTH_PROBE_ENABLED")
    service_health_probe_interval_seconds: float = Field(
        default=60.0, alias="COREMCP_SERVICE_HEALTH_PROBE_INTERVAL_SECONDS"
    )
    service_health_probe_timeout_seconds: float = Field(
        default=2.0, alias="COREMCP_SERVICE_HEALTH_PROBE_TIMEOUT_SECONDS"
    )
    initialize_downstream_timeout_seconds: float = Field(
        default=2.0, alias="COREMCP_INITIALIZE_DOWNSTREAM_TIMEOUT_SECONDS"
    )
    downstream_max_response_bytes: int = Field(
        default=1024 * 1024, alias="COREMCP_DOWNSTREAM_MAX_RESPONSE_BYTES"
    )
    max_request_body_bytes: int = Field(default=1024 * 1024, alias="COREMCP_MAX_REQUEST_BODY_BYTES")
    stdio_max_concurrent_processes: int = Field(
        default=8, alias="COREMCP_STDIO_MAX_CONCURRENT_PROCESSES"
    )
    stdio_default_idle_timeout_seconds: int = Field(
        default=300, alias="COREMCP_STDIO_DEFAULT_IDLE_TIMEOUT_SECONDS"
    )
    stdio_allowed_commands: str = Field(
        default=DEFAULT_STDIO_ALLOWED_COMMANDS,
        alias="COREMCP_STDIO_ALLOWED_COMMANDS",
    )
    auth_rate_limit_per_minute: int = Field(default=240, alias="COREMCP_AUTH_RATE_LIMIT_PER_MINUTE")
    mcp_rate_limit_per_minute: int = Field(default=120, alias="COREMCP_MCP_RATE_LIMIT_PER_MINUTE")
    service_rate_limit_per_minute: int = Field(default=120, alias="COREMCP_SERVICE_RATE_LIMIT_PER_MINUTE")
    downstream_session_ttl_seconds: int = Field(default=3600, alias="COREMCP_DOWNSTREAM_SESSION_TTL_SECONDS")
    cors_allowed_origins: str = Field(
        default=DEFAULT_CORS_ALLOWED_ORIGINS,
        alias="COREMCP_CORS_ALLOWED_ORIGINS",
    )
    allowed_hosts: str = Field(default=DEFAULT_ALLOWED_HOSTS, alias="COREMCP_ALLOWED_HOSTS")
    auth_mode: str = Field(default="static_bearer", alias="AUTH_MODE")
    oauth_dcr_enabled: bool = Field(default=True, alias="COREMCP_OAUTH_DCR_ENABLED")
    oauth_allowed_client_ids: str = Field(default="", alias="COREMCP_OAUTH_ALLOWED_CLIENT_IDS")
    expose_resource_metadata_in_static_mode: bool = Field(
        default=False, alias="EXPOSE_RESOURCE_METADATA_IN_STATIC_MODE"
    )
    metrics_enabled: bool = Field(default=False, alias="METRICS_ENABLED")
    ssrf_allow_hosts: str = Field(default="", alias="COREMCP_SSRF_ALLOW_HOSTS")
    ssrf_allow_cidrs: str = Field(default="", alias="COREMCP_SSRF_ALLOW_CIDRS")
    allow_tailscale_downstream: bool = Field(default=False, alias="ALLOW_TAILSCALE_DOWNSTREAM")
    icon_svg_enabled: bool = Field(default=False, alias="ICON_SVG_ENABLED")
    remote_tool_icons_enabled: bool = Field(default=False, alias="COREMCP_REMOTE_TOOL_ICONS_ENABLED")
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

    @property
    def cors_allowed_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]

    @property
    def stdio_allowed_command_set(self) -> set[str]:
        return {
            Path(command.strip()).name
            for command in self.stdio_allowed_commands.split(",")
            if command.strip()
        }

    @property
    def oauth_allowed_client_id_set(self) -> set[str]:
        return {
            client_id.strip()
            for client_id in self.oauth_allowed_client_ids.split(",")
            if client_id.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
