"""Tests for the root, health, and research placeholder endpoints."""


def test_root_endpoint(client) -> None:
    """The root endpoint describes the application."""
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "application": "AURORA",
        "version": "0.1.0",
        "description": "Autonomous Web Research and SEO Intelligence Platform",
        "status": "running",
    }


def test_health_endpoint(client) -> None:
    """Health works without any database or provider connectivity."""
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "application": "AURORA",
        "version": "0.1.0",
    }


def test_secrets_are_not_exposed_in_api_responses(monkeypatch) -> None:
    """No configured secret value appears in any response or the OpenAPI schema."""
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app
    from tests.auth_support import AUTH_HEADERS, TEST_API_TOKEN

    secrets = {
        "AURORA_API_TOKEN": TEST_API_TOKEN,
        "ANTHROPIC_API_KEY": "secret-anthropic-1",
        "GROQ_API_KEY": "secret-groq-2",
        "FIRECRAWL_API_KEY": "secret-firecrawl-3",
        "SERPAPI_API_KEY": "secret-serp-4",
        "NEO4J_PASSWORD": "secret-neo4j-5",
        "WORDPRESS_APPLICATION_PASSWORD": "secret-wp-6",
        "GOOGLE_CLIENT_SECRET": "secret-google-7",
        "GOOGLE_REFRESH_TOKEN": "secret-refresh-8",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    client = TestClient(create_app())

    responses = [
        client.get("/"),
        client.get("/api/v1/health"),
        client.post("/api/v1/research", json={"query": ""}, headers=AUTH_HEADERS),
        client.post("/api/v1/research", json={"query": ""}),  # unauthenticated
        client.get("/api/v1/graph/health", headers={"Authorization": "Bearer wrong"}),
        client.get("/openapi.json"),
    ]
    assert responses[2].status_code == 422
    assert responses[3].status_code == 401

    combined = " ".join(r.text for r in responses)
    assert not any(value in combined for value in secrets.values())
