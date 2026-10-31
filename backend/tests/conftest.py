"""Shared fixtures: isolate tests from the developer's real .env and environment."""

import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch):
    """Ignore the root .env and any AURORA-related environment variables."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
        monkeypatch.delenv(field.lower(), raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    """Create a test client for a freshly built application."""
    return TestClient(create_app())


@pytest.fixture
def env_keys() -> list[str]:
    """Names of environment variables currently set (for debugging)."""
    return sorted(os.environ)
