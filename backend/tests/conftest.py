"""Shared fixtures: isolate tests from the developer's real .env and environment."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.main import create_app
from app.tools.http import reset_circuit_breakers
from tests.intel_support import db_session  # noqa: F401  (shared fixture)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch):
    """Ignore the root .env and any AURORA-related environment variables."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
        monkeypatch.delenv(field.lower(), raising=False)
    get_settings.cache_clear()
    reset_circuit_breakers()
    yield
    get_settings.cache_clear()
    reset_circuit_breakers()


@pytest.fixture
def client() -> TestClient:
    """Create a test client for a freshly built application."""
    return TestClient(create_app())


@pytest.fixture
def keyed_settings() -> Settings:
    """Settings with fake provider keys and tiny retry budgets."""
    return Settings(
        serpapi_api_key=SecretStr("serp-secret-key-123"),
        firecrawl_api_key=SecretStr("fc-secret-key-456"),
        research_max_retries=2,
    )


@pytest.fixture
def sleeps() -> list[float]:
    """Recorded backoff delays (tools take ``sleeps.append``-style sleepers)."""
    return []


@pytest.fixture
def fake_sleep(sleeps: list[float]):
    """An awaitable sleep that records the delay instead of waiting."""

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    return sleep
