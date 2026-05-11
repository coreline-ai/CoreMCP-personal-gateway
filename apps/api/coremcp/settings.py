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
    downstream_timeout_seconds: float = Field(default=10.0, alias="COREMCP_DOWNSTREAM_TIMEOUT_SECONDS")

    @property
    def resolved_admin_token_file(self) -> Path:
        return self.admin_token_file.expanduser()

    @property
    def resolved_database_path(self) -> Path:
        return self.database_path.expanduser()


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
