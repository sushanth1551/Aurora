"""End-to-end analysis over seeded Module 3 data, mock LLMs and SQLite; plus API."""

import re
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.api.deps import get_analysis_service
from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.database.models import (
    IntelligenceAnalysisRecord,
    IntelligenceFindingRecord,
    IntelligenceReportRecord,
    LLMUsageRecord,
    ReportQualityEvaluationRecord,
    ResearchContextRecord,
    ResearchQuestionRecord,
)
from app.database.repositories.analysis_repository import AnalysisRepository
from app.database.session import get_session
from app.intelligence.followup import WebsiteFollowUpExecutor
from app.intelligence.models import AnalysisType
from app.llm.exceptions import ProviderUnavailableError
from app.llm.mock_provider import MockProvider
from app.main import create_app
from app.schemas.analysis import AnalyzeRequest
from tests.analysis_support import analysis_service, seed_acme_job
from tests.auth_support import AUTH_HEADERS
from tests.intel_support import (
    FakeCrawlProvider,
    FakeMapTool,
    acme_links,
    acme_pages,
    acme_search,
    build_service,
)

_ID = re.compile(r'id="(c-[0-9a-f]{10})"')


def ids(prompt: str, n: int = 2) -> list[str]:
    return _ID.findall(prompt)[:n]


def citing_responses() -> dict:
    """Mock replies that cite whichever claim ids the prompt actually contains."""
    return {
        "AnalysisOutput": lambda p: {
            "findings": [
                {
                    "description": "Acme publishes pricing and product information",
                    "kind": "observation",
                    "evidence_ids": ids(p),
                }
            ],
            "gaps": [],
            "contradictions": [],
            "answered_questions": [1],
            "sufficient": True,
            "sufficiency_rationale": "Enough public evidence for this objective.",
        },
        "SummaryOutput": lambda p: {
            "summary": [
                {
                    "statement": "Acme publishes pricing and product information",
                    "kind": "observation",
                    "evidence_ids": ids(p),
                }
            ]
        },
    }


def request(job_id, **kw) -> AnalyzeRequest:
    return AnalyzeRequest(
        research_job_id=job_id,
        research_objective="Analyse Acme products, pricing and customers",
        **kw,
    )


async def test_full_analysis_is_persisted_with_provenance(db_session) -> None:
    job = await seed_acme_job(db_session)
    provider = MockProvider(Settings(), responses=citing_responses())
    response = await analysis_service(db_session, provider).analyze(request(job))

    assert (
        response.status.value == "requires_review"
    )  # mock provider is never "completed"
    assert response.report_available and response.report_status.value in (
        "requires_review",
        "failed",
    )
    assert response.provider == "mock" and response.progress.stage == "completed"
    assert (
        response.progress.llm_calls == len(provider.calls)
        and response.progress.tokens_used > 0
    )

    aid = response.analysis_id
    repo = AnalysisRepository(db_session)
    for model in (ResearchContextRecord, ResearchQuestionRecord, IntelligenceFindingRecord,
                  IntelligenceReportRecord, LLMUsageRecord, ReportQualityEvaluationRecord):  # fmt: skip
        assert await repo.count_rows(model, aid) >= 1, model.__tablename__
    report, markdown = await repo.get_report(aid)
    assert report.research_job_id == job and report.metadata["mock_provider"]
    assert report.sources and all(
        s.source_url.startswith("https://acme.com") for s in report.sources
    )
    assert "## Source references" in markdown
    quality = await repo.get_quality(aid)
    assert quality.metrics.invalid_citation_count == 0
    usage = await repo.get_usage(aid)
    assert (
        usage.requests == len(provider.calls)
        and usage.estimated_cost is None
        and not usage.cost_complete
    )
    # the analysis row stores no prompts or source text
    record = await db_session.get(IntelligenceAnalysisRecord, aid)
    assert "Acme Automation is an AI automation company" not in str(
        record.configuration
    ) + str(record.progress)


async def test_report_is_never_persisted_twice(db_session) -> None:
    job = await seed_acme_job(db_session)
    response = await analysis_service(db_session).analyze(request(job))
    repo = AnalysisRepository(db_session)
    report, md = await repo.get_report(response.analysis_id)
    quality = (await repo.get_quality(response.analysis_id)).metrics
    from app.core.exceptions import PersistenceException

    with pytest.raises(PersistenceException):
        await repo.save_report(
            response.analysis_id,
            report.model_copy(update={"report_id": uuid4()}),
            md,
            quality,
        )
    assert await repo.count_rows(IntelligenceReportRecord, response.analysis_id) == 1


async def test_provider_outage_preserves_context_and_marks_partial(db_session) -> None:
    job = await seed_acme_job(db_session)
    down = ProviderUnavailableError("mock", "down")
    provider = MockProvider(Settings(llm_max_retries=0), script=[down] * 20)
    response = await analysis_service(db_session, provider).analyze(request(job))
    assert response.status.value == "partially_completed" and response.progress.degraded
    repo = AnalysisRepository(db_session)
    assert await repo.count_rows(ResearchContextRecord, response.analysis_id) == 1
    report, _ = await repo.get_report(response.analysis_id)
    assert report.comparisons and any("unavailable" in x for x in report.limitations)
    usage = await repo.get_usage(response.analysis_id)
    assert usage.requests >= 1 and not any(e.success for e in usage.entries)


async def test_job_without_evidence_fails_cleanly(db_session) -> None:
    from app.database.models import ResearchJobRecord
    from app.schemas.research import utc_now

    job_id = uuid4()
    db_session.add(
        ResearchJobRecord(
            id=job_id,
            query="empty",
            status="completed",
            max_results=1,
            scrape_pages=False,
            created_at=utc_now(),
        )
    )
    await db_session.commit()
    response = await analysis_service(db_session).analyze(request(job_id))
    assert response.status.value == "failed" and "no usable evidence" in response.error


async def test_followup_research_runs_through_module3(db_session) -> None:
    job = await seed_acme_job(db_session)
    module3, fakes = build_service(
        db_session,
        search=acme_search(),
        mapper=FakeMapTool(acme_links()),
        crawl=FakeCrawlProvider(acme_pages()),
    )
    calls = {"n": 0}

    def analysis_reply(p):
        calls["n"] += 1
        return {
            "findings": [
                {
                    "description": "Acme lists pricing",
                    "kind": "fact",
                    "evidence_ids": ids(p, 1),
                }
            ],
            "gaps": [
                {
                    "description": "Acme customer count unknown",
                    "related_entity": "Acme",
                    "predicate": "customers",
                }
            ],
            "sufficient": calls["n"] > 1,
        }

    provider = MockProvider(
        Settings(),
        responses={
            "AnalysisOutput": analysis_reply,
            "FollowupOutput": {
                "queries": [
                    {"query": "Acme automation customers", "target_name": "Acme"}
                ]
            },
        },
    )
    service = analysis_service(
        db_session, provider, executor=WebsiteFollowUpExecutor(module3)
    )
    response = await service.analyze(request(job, allow_follow_up_research=True))
    assert (
        response.progress.follow_up_queries == 1 and response.progress.iterations == 2
    )
    assert fakes["search"].calls  # the existing Module 3 search path was used
    repo = AnalysisRepository(db_session)
    assert await repo.count_rows(ResearchContextRecord, response.analysis_id) == 2


async def test_followup_not_run_unless_requested(db_session) -> None:
    job = await seed_acme_job(db_session)

    class Boom:
        async def execute(self, q):
            raise AssertionError("must not run")

    provider = MockProvider(
        Settings(),
        responses={
            "AnalysisOutput": {"gaps": [{"description": "Unknown pricing tiers"}]}
        },
    )
    response = await analysis_service(db_session, provider, executor=Boom()).analyze(
        request(job)
    )
    assert response.progress.stop_reason == "followup_disabled"


async def test_request_limits_cannot_exceed_server_limits(db_session) -> None:
    job = await seed_acme_job(db_session)
    s = Settings(intelligence_max_iterations=1, intelligence_max_sources=2)
    response = await analysis_service(db_session, settings=s).analyze(
        request(job, maximum_iterations=10, maximum_sources=500)
    )
    record = await db_session.get(IntelligenceAnalysisRecord, response.analysis_id)
    assert (
        record.configuration["max_iterations"] == 1
        and record.configuration["max_sources"] == 2
    )
    assert response.progress.sources <= 2


def test_request_schema_rejects_unsafe_fields() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AnalyzeRequest(
            research_job_id=uuid4(),
            research_objective="Analyse the companies",
            system_prompt="x",
        )  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        AnalyzeRequest(
            research_job_id=uuid4(),
            research_objective="Analyse the companies",
            preferred_model="gpt; rm -rf",
        )
    with pytest.raises(ValidationError):
        AnalyzeRequest(research_job_id=uuid4(), research_objective="short")


# -- API -------------------------------------------------------------------------------


@pytest.fixture
async def api(db_session):
    app = create_app()
    app.dependency_overrides[get_analysis_service] = lambda: analysis_service(
        db_session, MockProvider(Settings(), responses=citing_responses())
    )
    app.dependency_overrides[get_session] = lambda: db_session
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=AUTH_HEADERS
    ) as client:
        yield client


async def test_api_analyze_and_read_endpoints(api, db_session) -> None:
    job = await seed_acme_job(db_session)
    body = {
        "research_job_id": str(job),
        "research_objective": "Analyse Acme products and pricing",
        "analysis_type": AnalysisType.COMPANY_INTELLIGENCE.value,
    }
    created = await api.post("/api/v1/intelligence/analyze", json=body)
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["status"] == "requires_review" and data["provider"] == "mock"

    latest = (await api.get(f"/api/v1/intelligence/{job}")).json()
    assert latest["analysis_id"] == data["analysis_id"]
    report = await api.get(f"/api/v1/intelligence/{job}/report")
    assert (
        report.status_code == 200
        and report.json()["report_type"] == "company_intelligence"
    )
    markdown = await api.get(
        f"/api/v1/intelligence/{job}/report", params={"format": "markdown"}
    )
    assert markdown.headers["content-type"].startswith(
        "text/markdown"
    ) and markdown.text.startswith("# Company")
    usage = (await api.get(f"/api/v1/intelligence/{job}/usage")).json()
    assert usage["requests"] >= 1 and usage["estimated_cost"] is None
    quality = (await api.get(f"/api/v1/intelligence/{job}/quality")).json()
    assert "factual accuracy" in quality["note"]
    other = await api.get(
        f"/api/v1/intelligence/{job}/report", params={"analysis_id": str(uuid4())}
    )
    assert other.status_code == 404


async def test_api_errors_are_safe(api) -> None:
    missing = await api.post(
        "/api/v1/intelligence/analyze",
        json={
            "research_job_id": str(uuid4()),
            "research_objective": "Analyse nothing at all",
        },
    )
    assert (
        missing.status_code == 404
        and missing.json()["error"]["code"] == "resource_not_found"
    )
    invalid = await api.post(
        "/api/v1/intelligence/analyze",
        json={"research_job_id": "x", "research_objective": "y"},
    )
    assert invalid.status_code == 422
    assert (await api.get(f"/api/v1/intelligence/{uuid4()}")).status_code == 404


async def test_api_unconfigured_provider_is_503(db_session) -> None:
    job = await seed_acme_job(db_session)

    def unconfigured(*_a, **_k):
        raise ConfigurationException(
            "LLM provider 'openai' is not configured; set OPENAI_API_KEY and OPENAI_MODEL."
        )

    service = analysis_service(db_session)
    service._provider_builder = unconfigured
    app = create_app()
    app.dependency_overrides[get_analysis_service] = lambda: service
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=AUTH_HEADERS
    ) as client:
        r = await client.post(
            "/api/v1/intelligence/analyze",
            json={
                "research_job_id": str(job),
                "research_objective": "Analyse Acme products",
            },
        )
    assert (
        r.status_code == 503
        and "OPENAI_API_KEY" in r.text
        and "Traceback" not in r.text
    )
    assert (
        await db_session.execute(select(IntelligenceAnalysisRecord))
    ).first() is None
