"""Tests for the root, health, and research placeholder endpoints."""

import pytest


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


def test_research_endpoint_is_a_clearly_marked_placeholder(client) -> None:
    """The research endpoint validates input but performs no research."""
    response = client.post("/api/v1/research", json={"topic": "Aurora SEO"})
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "not_implemented"
    assert body["is_placeholder"] is True
    assert body["topic"] == "Aurora SEO"
    assert "later module" in body["message"]
    assert {"request_id", "created_at"} <= body.keys()


@pytest.mark.parametrize(
    "payload",
    [
        {"topic": " "},
        {"topic": "x"},
        {},
        {"topic": "Valid topic", "max_sources": 0},
        {"topic": "Valid topic", "max_sources": 1000},
        {"topic": "Valid topic", "research_depth": "exhaustive"},
        {"topic": "Valid topic", "unexpected": True},
    ],
)
def test_research_endpoint_rejects_invalid_input(client, payload) -> None:
    """Invalid payloads are rejected by the request schema."""
    assert client.post("/api/v1/research", json=payload).status_code == 422


def test_research_endpoint_accepts_optional_fields(client) -> None:
    """Optional fields are accepted."""
    response = client.post(
        "/api/v1/research",
        json={
            "topic": "Aurora SEO",
            "description": "Context",
            "research_depth": "deep",
            "max_sources": 5,
        },
    )

    assert response.status_code == 200


def test_secrets_are_not_exposed_in_api_responses(monkeypatch) -> None:
    """No configured secret value appears in any response or the OpenAPI schema."""
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    secrets = {
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
        client.post("/api/v1/research", json={"topic": "Aurora SEO"}),
        client.post("/api/v1/research", json={"topic": "x"}),
        client.get("/openapi.json"),
    ]

    combined = " ".join(r.text for r in responses)
    assert not any(value in combined for value in secrets.values())
