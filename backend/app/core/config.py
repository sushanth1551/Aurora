"""Central, environment-backed application settings.

Settings are read from the process environment and the repository-root ``.env``
file. Every external credential is optional so the API starts without any
provider, database, or publishing configuration. No clients are created here.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
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
        "postgresql+asyncpg://aurora:aurora_dev_password@localhost:5432/aurora"
    )
    database_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
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

    research_max_results: int = Field(default=10, ge=1, le=100)
    research_max_pages: int = Field(default=10, ge=1, le=50)
    research_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    research_max_retries: int = Field(default=3, ge=0, le=6)
    research_retry_base_delay_seconds: float = Field(default=0.5, gt=0, le=30)
    research_retry_max_delay_seconds: float = Field(default=8.0, gt=0, le=120)
    research_max_concurrent_extractions: int = Field(default=3, ge=1, le=10)
    research_max_content_chars: int = Field(default=200_000, ge=1_000)
    research_http_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    research_http_read_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    firecrawl_scrape_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    firecrawl_max_concurrent_requests: int = Field(default=2, ge=1, le=10)
    serpapi_max_concurrent_requests: int = Field(default=2, ge=1, le=10)
    provider_circuit_failure_threshold: int = Field(default=5, ge=0, le=100)
    provider_circuit_cooldown_seconds: float = Field(default=30.0, gt=0, le=3600)
    research_dns_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    research_max_redirects: int = Field(default=5, ge=0, le=10)
    research_preflight_redirects: bool = False

    website_research_max_websites: int = Field(default=10, ge=1, le=50)
    website_research_max_pages_per_site: int = Field(default=20, ge=1, le=100)
    website_research_max_total_pages: int = Field(default=100, ge=1, le=1000)
    website_research_max_crawl_depth: int = Field(default=2, ge=0, le=6)
    website_research_max_concurrent_sites: int = Field(default=2, ge=1, le=10)
    website_research_max_concurrent_pages: int = Field(default=2, ge=1, le=10)
    website_research_max_concurrent_searches: int = Field(default=2, ge=1, le=5)
    website_research_max_retries: int = Field(default=1, ge=0, le=3)
    website_research_cache_ttl_seconds: float = Field(default=600.0, ge=0, le=86400)
    website_research_cache_max_entries: int = Field(default=128, ge=1, le=5000)
    website_research_timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    website_research_map_timeout_seconds: float = Field(default=120.0, gt=0, le=300)
    website_research_batch_timeout_seconds: float = Field(default=120.0, gt=0, le=900)
    website_research_max_content_size_mb: float = Field(default=5.0, gt=0, le=50)
    website_research_max_map_urls: int = Field(default=100, ge=1, le=5000)
    website_research_enable_llm_extraction: bool = False
    website_research_enable_reports: bool = True
    website_research_respect_robots: bool = True
    website_research_batch_poll_seconds: float = Field(default=2.0, gt=0, le=30)
    website_research_batch_max_polls: int = Field(default=30, ge=1, le=300)
    website_research_max_provider_requests: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def _check_consistency(self) -> "Settings":
        """Reject combinations that would make limits meaningless or contradictory."""
        problems: list[str] = []
        if (
            self.research_retry_max_delay_seconds
            < self.research_retry_base_delay_seconds
        ):
            problems.append("RESEARCH_RETRY_MAX_DELAY_SECONDS < ..._BASE_DELAY_SECONDS")
        if self.website_research_batch_timeout_seconds < (
            self.website_research_batch_poll_seconds
        ):
            problems.append("WEBSITE_RESEARCH_BATCH_TIMEOUT_SECONDS < ..._POLL_SECONDS")
        if problems:
            raise ValueError("inconsistent settings: " + "; ".join(problems))
        return self

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
