"""API tests: dependency-overridden service, no network or database."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_research_service
from app.core.config import Settings
from app.main import create_app
from app.research.research_service import ResearchService
from app.research.source_manager import SourceManager
from app.tools.errors import ProviderAuthenticationError, ProviderRateLimitError
from tests.fakes import (
    FakeExtractor,
    FakeSearch,
    InMemoryRepository,
    failed_page,
    make_policy,
    persistence_down,
    result,
)

URLS = ["https://a.example.com/x", "https://b.example.com/y"]


def make_client(
    search=None, extractor=None, repo=None
) -> tuple[TestClient, InMemoryRepository]:
    repo = repo or InMemoryRepository()
    service = ResearchService(
        search=search or FakeSearch([result(i, u) for i, u in enumerate(URLS, 1)]),
        extractor=extractor or FakeExtractor(),
        sources=SourceManager(make_policy()),
        repository=repo,
        settings=Settings(),
    )
    app = create_app()
    app.dependency_overrides[get_research_service] = lambda: service
    return TestClient(app, raise_server_exceptions=False), repo


def test_post_research_success_structure() -> None:
    client, _ = make_client()

    response = client.post(
        "/api/v1/research",
        json={
            "query": "Best AI automation tools",
            "max_results": 5,
            "country": "in",
            "language": "en",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed" and body["query"] == "Best AI automation tools"
    assert body["successful_source_count"] == 2 and len(body["search_results"]) == 2
    assert {"job_id", "stages", "errors", "created_at", "completed_at"} <= body.keys()
    source = body["sources"][0]
    assert (
        source["original_url"] == URLS[0] and source["extraction_status"] == "succeeded"
    )
    assert source["extracted_markdown"] and source["content_hash"]


def test_post_research_partial_is_still_200() -> None:
    client, _ = make_client(extractor=FakeExtractor({URLS[0]: failed_page(URLS[0])}))

    response = client.post("/api/v1/research", json={"query": "q"})

    assert response.status_code == 200
    assert response.json()["status"] == "partially_completed"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": ""},
        {"query": "x", "max_results": 0},
        {"query": "x", "max_results": 1000},
        {"query": "x", "country": "india"},
        {"query": "x", "allowed_domains": ["bad domain"]},
        {"query": "x", "extra": 1},
        {"topic": "old field name"},
    ],
)
def test_post_research_validates_input(payload) -> None:
    client, repo = make_client()

    assert client.post("/api/v1/research", json=payload).status_code == 422
    assert repo.jobs == {}  # nothing was started


@pytest.mark.parametrize(
    "error",
    [
        ProviderAuthenticationError("SerpApi", "credentials rejected"),
        ProviderRateLimitError("SerpApi", "rate limited"),
    ],
)
def test_provider_outage_returns_503_with_job_details(error) -> None:
    client, _ = make_client(search=FakeSearch(error=error))

    response = client.post("/api/v1/research", json={"query": "q"})

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "failed" and body["failure"]["stage"] == "search"
    assert body["job_id"]


def test_non_outage_failure_is_200_with_failed_status() -> None:
    client, _ = make_client(search=FakeSearch([result(1, "http://127.0.0.1/")]))

    response = client.post("/api/v1/research", json={"query": "q"})

    assert response.status_code == 200
    assert response.json()["failure"]["category"] == "no_eligible_urls"


def test_database_failure_returns_503_without_internals() -> None:
    repo = InMemoryRepository()
    repo.fail_create = persistence_down()
    client, _ = make_client(repo=repo)

    response = client.post("/api/v1/research", json={"query": "q"})

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "persistence_error",
            "message": "The database is unavailable.",
        }
    }


def test_unexpected_service_error_is_a_generic_500() -> None:
    class Boom:
        async def run(self, _request):
            raise RuntimeError("postgres://user:hunter2@internal-host/db exploded")

    app = create_app()
    app.dependency_overrides[get_research_service] = lambda: Boom()
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/v1/research", json={"query": "q"})

    assert response.status_code == 500
    assert "hunter2" not in response.text and "internal-host" not in response.text


def test_get_job_roundtrip_and_content_flag() -> None:
    client, _ = make_client()
    job_id = client.post("/api/v1/research", json={"query": "q"}).json()["job_id"]

    full = client.get(f"/api/v1/research/{job_id}")
    light = client.get(
        f"/api/v1/research/{job_id}", params={"include_content": "false"}
    )

    assert full.status_code == 200 and full.json()["job_id"] == job_id
    assert full.json()["sources"][0]["extracted_markdown"]
    assert light.json()["sources"][0]["extracted_markdown"] is None


def test_get_missing_job_is_404() -> None:
    client, _ = make_client()

    response = client.get(f"/api/v1/research/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


def test_get_invalid_job_id_is_422() -> None:
    client, _ = make_client()
    assert client.get("/api/v1/research/not-a-uuid").status_code == 422


def test_request_id_is_echoed_or_generated() -> None:
    client, _ = make_client()

    echoed = client.get("/", headers={"X-Request-ID": "trace-abc-123"})
    generated = client.get("/")
    unsafe = client.get("/", headers={"X-Request-ID": "bad id\nwith newline"})

    assert echoed.headers["x-request-id"] == "trace-abc-123"
    assert len(generated.headers["x-request-id"]) == 32
    assert unsafe.headers["x-request-id"] != "bad id\nwith newline"


def test_idempotent_request_id_over_http() -> None:
    client, _ = make_client()
    payload = {"query": "q", "request_id": "idem-http-0001"}

    first = client.post("/api/v1/research", json=payload).json()
    second = client.post("/api/v1/research", json=payload).json()

    assert first["job_id"] == second["job_id"]


def test_openapi_documents_research_routes() -> None:
    paths = create_app().openapi()["paths"]

    assert "post" in paths["/api/v1/research"]
    assert "get" in paths["/api/v1/research/{job_id}"]
    assert "503" in paths["/api/v1/research"]["post"]["responses"]


def test_root_and_health_still_work() -> None:
    client, _ = make_client()

    assert client.get("/").json()["status"] == "running"
    assert client.get("/api/v1/health").json()["status"] == "healthy"
