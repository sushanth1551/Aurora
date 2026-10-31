"""Central, environment-backed application settings.

Settings are read from the process environment and the repository-root ``.env``
file. Every external credential is optional so the API starts without any
provider, database, or publishing configuration. No clients are created here.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> repository root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"

APP_DESCRIPTION = "Autonomous Web Research and SEO Intelligence Platform"


class Settings(BaseSettings):
    """Validated runtime configuration; secrets are optional in local mode."""

    app_name: str = "AURORA"
    app_version: str = "0.1.0"
    app_description: str = APP_DESCRIPTION
    app_env: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://aurora:aurora@localhost:5432/aurora"
    )
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: SecretStr | None = Field(default=None)

    firecrawl_api_key: SecretStr | None = Field(default=None)
    serpapi_api_key: SecretStr | None = Field(default=None)
    groq_api_key: SecretStr | None = Field(default=None)
    anthropic_api_key: SecretStr | None = Field(default=None)

    wordpress_url: str | None = None
    wordpress_username: str | None = None
    wordpress_application_password: SecretStr | None = Field(default=None)

    google_client_id: str | None = None
    google_client_secret: SecretStr | None = Field(default=None)
    google_refresh_token: SecretStr | None = Field(default=None)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings loaded from the environment and root ``.env``."""
    return Settings()
