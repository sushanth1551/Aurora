"""API tests for the website intelligence endpoints (ASGI client + SQLite)."""

from uuid import uuid4

import httpx
import pytest

from app.api.deps import get_intelligence_service
from app.database.session import get_session
from app.main import create_app
from app.tools.errors import ProviderAuthenticationError
from tests.intel_support import (
    FakeCrawlProvider,
    FakeMapTool,
    QuerySearch,
    acme_links,
    acme_pages,
    acme_search,
    build_service,
)

BASE = "/api/v1/research"


@pytest.fixture
async def api(db_session):
    """An ASGI client whose intelligence service uses fakes and the SQLite session."""
    service, fakes = build_service(
        db_session,
        search=acme_search(),
        mapper=FakeMapTool(acme_links()),
        crawl=FakeCrawlProvider(acme_pages()),
    )
    app = create_app()
    app.dependency_overrides[get_intelligence_service] = lambda: service
    app.dependency_overrides[get_session] = lambda: db_session  # Module 2 routes
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.service, client.fakes, client.app_ = service, fakes, app  # type: ignore[attr-defined]
        yield client


async def run_full(api) -> dict:
    response = await api.post(
        f"{BASE}/intelligence", json={"query": "Acme", "target_names": ["Acme"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_full_workflow_and_all_read_endpoints(api) -> None:
    summary = await run_full(api)
    job = summary["job_id"]

    assert summary["status"] == "completed" and summary["report_available"] is True
    assert summary["websites"] == 1 and summary["crawled_pages"] == 4
    assert summary["coverage"]["total_claims"] > 0 and len(summary["stages"]) == 7

    progress = (await api.get(f"{BASE}/{job}/progress")).json()
    assert progress["status"] == "completed" and progress["job_id"] == job

    websites = (await api.get(f"{BASE}/{job}/websites")).json()
    assert (
        websites["total"] == 1
        and websites["items"][0]["candidate"]["domain"] == "acme.com"
    )
    assert websites["items"][0]["candidate"]["official_status"] == "verified"

    entities = (
        await api.get(f"{BASE}/{job}/entities", params={"entity_type": "company"})
    ).json()
    assert (
        entities["total"] == 1
        and entities["items"][0]["attributes"]["founded_year"] == 2016
    )

    claims = (
        await api.get(f"{BASE}/{job}/claims", params={"predicate": "headquarters"})
    ).json()
    claim = claims["items"][0]
    assert (
        claim["verification_status"] == "supported"
        and claim["source_url"] == "https://acme.com/"
    )
    assert "headquartered in Bengaluru" in claim["evidence_excerpt"]
    assert claim["source_domain"] == "acme.com" and claim["observed_at"]

    report = (await api.get(f"{BASE}/{job}/report")).json()
    assert (
        report["report_type"] == "company_profile"
        and report["data"]["companies"][0]["name"] == "Acme"
    )
    markdown = await api.get(f"{BASE}/{job}/report", params={"format": "markdown"})
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert markdown.text.startswith("# Company Profile")


async def test_staged_endpoints(api) -> None:
    discovered = await api.post(
        f"{BASE}/websites",
        json={"query": "Acme", "target_names": ["Acme"], "country": "in"},
    )
    assert discovered.status_code == 200
    body = discovered.json()
    website_id = body["websites"][0]["website_id"]
    assert (
        body["status"] == "completed"
        and body["websites"][0]["candidate"]["homepage_url"] == "https://acme.com/"
    )

    mapped = await api.post(f"{BASE}/websites/{website_id}/map", json={"max_urls": 50})
    assert mapped.status_code == 200 and mapped.json()["eligible_pages"] == 4
    assert {p["page_type"] for p in mapped.json()["pages"]} >= {"pricing", "product"}

    crawled = await api.post(
        f"{BASE}/websites/{website_id}/crawl",
        json={"max_pages": 2, "page_types": ["pricing", "homepage"]},
    )
    assert crawled.status_code == 200
    assert (
        crawled.json()["crawl"]["successful_pages"] == 2
        and crawled.json()["claims"] > 0
    )

    assert (await api.get(f"{BASE}/{body['job_id']}/report")).status_code == 200


async def test_crawl_before_map_is_a_clear_422(api) -> None:
    discovered = (
        await api.post(
            f"{BASE}/websites", json={"query": "Acme", "target_names": ["Acme"]}
        )
    ).json()

    response = await api.post(
        f"{BASE}/websites/{discovered['websites'][0]['website_id']}/crawl", json={}
    )

    assert (
        response.status_code == 422
        and "map it first" in response.json()["error"]["message"]
    )


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/websites", {}),
        ("/websites", {"query": "x"}),
        ("/websites", {"query": "Acme", "max_websites": 0}),
        ("/websites", {"query": "Acme", "subject_type": "alien"}),
        ("/websites", {"query": "Acme", "unknown": 1}),
        ("/websites", {"query": "Acme", "target_names": ["x" * 200]}),
        ("/intelligence", {"query": "Acme", "max_pages_per_site": 1000}),
        ("/intelligence", {"query": "Acme", "website_urls": ["https://a.com"] * 21}),
        ("/intelligence", {"query": "Acme", "report_type": "gossip"}),
    ],
)
async def test_request_validation(api, path, payload) -> None:
    assert (await api.post(f"{BASE}{path}", json=payload)).status_code == 422


@pytest.mark.parametrize(
    ("suffix", "params"),
    [
        ("websites", {"limit": 0}),
        ("websites", {"limit": 201}),
        ("entities", {"offset": -1}),
        ("entities", {"entity_type": "spaceship"}),
        ("claims", {"status": "verified"}),
        ("report", {"format": "pdf"}),
        ("report", {"report_type": "gossip"}),
    ],
)
async def test_query_parameter_validation(api, suffix, params) -> None:
    job = (await run_full(api))["job_id"]

    assert (await api.get(f"{BASE}/{job}/{suffix}", params=params)).status_code == 422


async def test_pagination(api) -> None:
    job = (await run_full(api))["job_id"]

    page = (
        await api.get(f"{BASE}/{job}/claims", params={"limit": 3, "offset": 0})
    ).json()
    rest = (
        await api.get(f"{BASE}/{job}/claims", params={"limit": 3, "offset": 3})
    ).json()

    assert len(page["items"]) == 3 and page["total"] > 3 and page["limit"] == 3
    assert {c["claim_id"] for c in page["items"]}.isdisjoint(
        {c["claim_id"] for c in rest["items"]}
    )


@pytest.mark.parametrize(
    "suffix", ["progress", "websites", "entities", "claims", "report"]
)
async def test_unknown_job_is_404_and_bad_uuid_is_422(api, suffix) -> None:
    missing = await api.get(f"{BASE}/{uuid4()}/{suffix}")

    assert (
        missing.status_code == 404
        and missing.json()["error"]["code"] == "resource_not_found"
    )
    assert (await api.get(f"{BASE}/not-a-uuid/{suffix}")).status_code == 422


async def test_unknown_website_is_404(api) -> None:
    assert (
        await api.post(f"{BASE}/websites/{uuid4()}/map", json={})
    ).status_code == 404
    assert (
        await api.post(f"{BASE}/websites/{uuid4()}/crawl", json={})
    ).status_code == 404


async def test_provider_failure_is_a_safe_502(api) -> None:
    api.fakes["search"].error = ProviderAuthenticationError(
        "SerpApi", "credentials rejected or access denied"
    )  # noqa: E501

    response = await api.post(
        f"{BASE}/websites", json={"query": "Acme", "target_names": ["Acme"]}
    )

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "provider_authentication_error"
    assert (
        "api_key" not in response.text.lower()
        and "traceback" not in response.text.lower()
    )


async def test_unexpected_errors_are_generic_500(api) -> None:
    class Boom:
        async def run(self, _request):
            raise RuntimeError("postgres://user:hunter2@internal/db exploded")

    api.app_.dependency_overrides[get_intelligence_service] = lambda: Boom()

    response = await api.post(f"{BASE}/intelligence", json={"query": "Acme"})

    assert response.status_code == 500
    assert "hunter2" not in response.text and "postgres://" not in response.text
    assert response.json()["error"]["message"] == "An unexpected error occurred."


async def test_module2_job_endpoint_still_works_for_intelligence_jobs(api) -> None:
    job = (await run_full(api))["job_id"]

    response = await api.get(f"{BASE}/{job}")

    assert response.status_code == 200 and response.json()["job_id"] == job
    assert response.json()["status"] in (
        "completed",
        "running",
        "partially_completed",
        "failed",
    )


async def test_request_id_header_and_openapi(api) -> None:
    response = await api.get(
        f"{BASE}/{uuid4()}/progress", headers={"X-Request-ID": "trace-xyz-1"}
    )
    assert response.headers["x-request-id"] == "trace-xyz-1"

    paths = (await api.get("/openapi.json")).json()["paths"]
    for path in (
        f"{BASE}/websites", f"{BASE}/websites/{{website_id}}/map", f"{BASE}/websites/{{website_id}}/crawl",
        f"{BASE}/intelligence", f"{BASE}/{{job_id}}/websites", f"{BASE}/{{job_id}}/entities",
        f"{BASE}/{{job_id}}/claims", f"{BASE}/{{job_id}}/report", f"{BASE}/{{job_id}}/progress",
    ):  # fmt: skip
        assert path in paths, path
    # Module 2 routes are untouched
    assert "post" in paths[BASE] and "get" in paths[f"{BASE}/{{job_id}}"]


async def test_module1_and_module2_endpoints_unaffected(api) -> None:
    assert (await api.get("/")).json()["status"] == "running"
    assert (await api.get("/api/v1/health")).json()["status"] == "healthy"
    assert (await api.post(BASE, json={"query": ""})).status_code == 422
    assert QuerySearch  # keep import used for type checkers
