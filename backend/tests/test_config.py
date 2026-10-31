"""Tests for settings loading and application initialization."""

import pytest

from app.core.config import ENV_FILE, Settings, get_settings
from app.main import create_app

SECRET_FIELDS = (
    "neo4j_password",
    "firecrawl_api_key",
    "serpapi_api_key",
    "groq_api_key",
    "anthropic_api_key",
    "wordpress_application_password",
    "google_client_secret",
    "google_refresh_token",
)


def test_settings_load_with_missing_optional_credentials() -> None:
    """Settings need no provider credentials."""
    settings = Settings()

    assert settings.app_name == "AURORA"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.app_env == "development"
    for field in SECRET_FIELDS:
        assert getattr(settings, field) is None


def test_settings_read_case_insensitive_environment(monkeypatch) -> None:
    """Environment variables override defaults regardless of case."""
    monkeypatch.setenv("log_level", "DEBUG")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-value")

    settings = Settings()

    assert settings.log_level == "DEBUG"
    assert settings.groq_api_key is not None
    assert settings.groq_api_key.get_secret_value() == "test-groq-value"


def test_secrets_are_masked_in_repr(monkeypatch) -> None:
    """Secret values never appear in repr or string output."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret-anthropic")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:super-secret-db@h/db")

    rendered = repr(Settings()) + str(Settings())

    assert "super-secret" not in rendered


def test_env_file_is_repository_root() -> None:
    """The .env path points at the repository root, not the working directory."""
    assert ENV_FILE.name == ".env"
    assert (ENV_FILE.parent / "pyproject.toml").exists()


def test_get_settings_is_cached() -> None:
    """The cached getter returns one shared instance."""
    assert get_settings() is get_settings()


def test_application_initializes_without_external_credentials() -> None:
    """Creating the ASGI app does not connect to external systems."""
    application = create_app()

    assert application.title == "AURORA"
    assert application.version == "0.1.0"
    assert "/api/v1/health" in application.openapi()["paths"]


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_api_documentation_is_available(client, path: str) -> None:
    """Swagger UI and the OpenAPI schema are served."""
    assert client.get(path).status_code == 200
