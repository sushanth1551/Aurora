"""Central, environment-backed application settings.

Settings are read from the process environment and the repository-root ``.env``
file. Every external credential is optional so the API starts without any
provider, database, or publishing configuration. No clients are created here.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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
    openai_api_key: SecretStr | None = Field(default=None)

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

    # Module 4: LLM providers and the intelligence engine. Defaults are safe: the
    # deterministic mock provider needs no credentials and costs nothing.
    llm_provider: Literal["openai", "anthropic", "groq", "mock"] = "mock"
    openai_model: str | None = None
    anthropic_model: str | None = None
    #: Groq uses its OpenAI-compatible Chat Completions API (see llm/groq_provider.py).
    groq_model: str | None = "openai/gpt-oss-20b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    #: ``json_schema`` (best-effort, for gpt-oss models) or ``json_object`` (JSON mode).
    groq_structured_output: Literal["json_schema", "json_object"] = "json_schema"
    #: Ask Groq not to return reasoning text (``include_reasoning: false``).
    groq_exclude_reasoning: bool = True
    groq_reasoning_effort: Literal["low", "medium", "high"] | None = None
    llm_request_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    llm_max_retries: int = Field(default=3, ge=0, le=6)
    llm_retry_base_delay_seconds: float = Field(default=1.0, gt=0, le=30)
    llm_retry_max_delay_seconds: float = Field(default=20.0, gt=0, le=120)
    llm_max_output_tokens: int = Field(default=4096, ge=64, le=64000)
    llm_enable_provider_fallback: bool = False
    llm_fallback_provider: Literal["openai", "anthropic", "groq", "mock"] = "anthropic"
    #: JSON object ``{"provider:model": {"input_per_million": n, ...}}``; no prices
    #: are built in, so costs are reported as unavailable until this is set.
    llm_pricing_json: str | None = None

    intelligence_max_iterations: int = Field(default=3, ge=1, le=10)
    intelligence_max_followup_queries: int = Field(default=5, ge=0, le=20)
    intelligence_max_total_queries: int = Field(default=10, ge=0, le=50)
    intelligence_max_duration_seconds: float = Field(default=300.0, gt=0, le=3600)
    intelligence_max_llm_calls: int = Field(default=15, ge=1, le=200)
    intelligence_max_token_budget: int = Field(default=50_000, ge=1000, le=5_000_000)
    intelligence_max_estimated_cost: float | None = Field(default=None, gt=0)
    #: Global evidence-token budget for the research context (backward compatible).
    intelligence_context_max_tokens: int = Field(default=12_000, ge=500, le=200_000)
    #: Per-provider overrides of the evidence budget; ``None`` means "use the
    #: global value". These are *application* budgets for the evidence block
    #: (see ``Settings.context_budget_for``), not provider context windows or
    #: rate limits. Groq's default is small because its per-minute token
    #: allowance on the entry tier is small; raise it for a higher tier.
    intelligence_context_max_tokens_groq: int | None = Field(
        default=4_000, ge=500, le=200_000
    )
    intelligence_context_max_tokens_openai: int | None = Field(
        default=None, ge=500, le=200_000
    )
    intelligence_context_max_tokens_anthropic: int | None = Field(
        default=None, ge=500, le=200_000
    )
    intelligence_context_max_tokens_mock: int | None = Field(
        default=None, ge=500, le=200_000
    )
    intelligence_max_sources: int = Field(default=40, ge=1, le=500)
    intelligence_enable_followup_research: bool = True
    intelligence_enable_llm_evaluation: bool = False
    intelligence_min_citation_coverage: float = Field(default=0.8, ge=0, le=1)
    intelligence_max_unsupported_claims: int = Field(default=0, ge=0)
    intelligence_freshness_days: int = Field(default=365, ge=1, le=3650)

    def context_budget_for(self, provider: str) -> int:
        """Evidence-token budget for ``provider``: its override, else the global value.

        The budget bounds only the ``<evidence>`` block that the context builder
        selects. The system prompt, task instructions, objective and engine-built
        sections are added on top of it per request (and are included in the
        separate pre-flight check against ``INTELLIGENCE_MAX_TOKEN_BUDGET``).
        """
        override = getattr(self, f"intelligence_context_max_tokens_{provider}", None)
        return int(override) if override else self.intelligence_context_max_tokens

    @field_validator(
        "openai_model",
        "anthropic_model",
        "groq_model",
        "groq_reasoning_effort",
        "llm_pricing_json",
        "intelligence_max_estimated_cost",
        "intelligence_context_max_tokens_groq",
        "intelligence_context_max_tokens_openai",
        "intelligence_context_max_tokens_anthropic",
        "intelligence_context_max_tokens_mock",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """``KEY=`` in ``.env`` means "not set" for these optional values."""
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("groq_base_url")
    @classmethod
    def _groq_base_url(cls, value: str) -> str:
        """HTTPS only; plain HTTP is allowed for a local test server."""
        value = value.strip().rstrip("/")
        local = value.startswith(("http://127.0.0.1", "http://localhost"))
        if not (value.startswith("https://") or local):
            raise ValueError("GROQ_BASE_URL must use https://")
        return value

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
        if self.llm_retry_max_delay_seconds < self.llm_retry_base_delay_seconds:
            problems.append("LLM_RETRY_MAX_DELAY_SECONDS < ..._BASE_DELAY_SECONDS")
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
