"""Tests for the research service with injected fakes (no network, no database)."""

import asyncio
import logging

import pytest

from app.core.config import Settings
from app.core.exceptions import PersistenceException, ResourceNotFoundException
from app.research.research_service import INFRASTRUCTURE_CATEGORIES, ResearchService
from app.research.source_manager import SourceManager
from app.schemas.research import (
    ExtractionStatus,
    JobStatus,
    ResearchRequest,
    ResearchStage,
    StageStatus,
)
from app.tools.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from tests.fakes import (
    FakeExtractor,
    FakeSearch,
    InMemoryRepository,
    failed_page,
    make_policy,
    ok_page,
    persistence_down,
    result,
)

URLS = [f"https://site{i}.example.com/page" for i in range(1, 5)]


def build(
    search=None,
    extractor=None,
    repo=None,
    policy=None,
    **settings,
) -> tuple[ResearchService, FakeSearch, FakeExtractor, InMemoryRepository]:
    search = search or FakeSearch([result(i, u) for i, u in enumerate(URLS, 1)])
    extractor = extractor or FakeExtractor()
    repo = repo or InMemoryRepository()
    service = ResearchService(
        search=search,
        extractor=extractor,
        sources=SourceManager(policy or make_policy()),
        repository=repo,
        settings=Settings(**settings),
    )
    return service, search, extractor, repo


def stage(response, name: ResearchStage):
    return next(s for s in response.stages if s.stage == name)


async def test_full_success_with_provenance() -> None:
    service, search, extractor, repo = build()

    response = await service.run(ResearchRequest(query="ai tools", country="in"))

    assert response.status == JobStatus.COMPLETED and response.failure is None
    assert response.actual_result_count == 4 and response.successful_source_count == 4
    assert response.failed_source_count == 0
    assert search.calls == [
        {"query": "ai tools", "num": 10, "country": "in", "language": None}
    ]
    for source, url in zip(response.sources, URLS, strict=True):
        assert source.original_url == url and source.normalized_url == url
        assert source.extraction_status == ExtractionStatus.SUCCEEDED
        assert source.content_hash and source.extracted_markdown and source.domain
    assert [s.stage for s in response.stages] == [
        ResearchStage.SEARCH,
        ResearchStage.VALIDATION,
        ResearchStage.EXTRACTION,
        ResearchStage.PERSISTENCE,
    ]
    assert all(s.status == StageStatus.SUCCEEDED for s in response.stages)
    assert (await service.get(response.job_id)).status == JobStatus.COMPLETED
    assert repo.jobs[response.job_id].completed_at is not None


async def test_extraction_concurrency_is_passed_from_settings() -> None:
    service, _, extractor, _ = build(research_max_concurrent_extractions=2)

    await service.run(ResearchRequest(query="q"))

    assert extractor.concurrency == 2


async def test_partial_extraction_is_partially_completed() -> None:
    extractor = FakeExtractor(
        {URLS[1]: failed_page(URLS[1]), URLS[2]: failed_page(URLS[2])}
    )
    service, *_ = build(extractor=extractor)

    response = await service.run(ResearchRequest(query="q"))

    assert response.status == JobStatus.PARTIALLY_COMPLETED and response.failure is None
    assert (response.successful_source_count, response.failed_source_count) == (2, 2)
    failed = [
        s for s in response.sources if s.extraction_status == ExtractionStatus.FAILED
    ]
    assert len(failed) == 2 and all(s.error_message for s in failed)
    assert response.errors[0].category == "partial_extraction"
    assert stage(response, ResearchStage.EXTRACTION).status == StageStatus.SUCCEEDED


async def test_every_extraction_failing_is_failed_but_sources_are_kept() -> None:
    extractor = FakeExtractor({u: failed_page(u, "empty_content") for u in URLS})
    service, *_ = build(extractor=extractor)

    response = await service.run(ResearchRequest(query="q"))

    assert response.status == JobStatus.FAILED
    assert response.failure.category == "empty_content"
    assert len(response.sources) == 4 and response.successful_source_count == 0
    assert stage(response, ResearchStage.EXTRACTION).status == StageStatus.FAILED


async def test_extractor_provider_outage_marks_pages_failed_with_infra_category() -> (
    None
):
    extractor = FakeExtractor(error=ProviderUnavailableError("Firecrawl", "down"))
    service, *_ = build(extractor=extractor)

    response = await service.run(ResearchRequest(query="q"))

    assert response.status == JobStatus.FAILED
    assert response.failure.category in INFRASTRUCTURE_CATEGORIES


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (
            ProviderAuthenticationError("SerpApi", "bad key"),
            "provider_authentication_error",
        ),
        (ProviderRateLimitError("SerpApi", "slow down"), "provider_rate_limited"),
        (ProviderUnavailableError("SerpApi", "down"), "provider_unavailable"),
    ],
)
async def test_search_provider_failures(error, category) -> None:
    service, _, extractor, repo = build(search=FakeSearch(error=error))

    response = await service.run(ResearchRequest(query="q"))

    assert response.status == JobStatus.FAILED
    assert (
        response.failure.category == category and category in INFRASTRUCTURE_CATEGORIES
    )
    assert response.failure.stage == ResearchStage.SEARCH
    assert stage(response, ResearchStage.SEARCH).status == StageStatus.FAILED
    assert extractor.calls == []  # nothing fetched after a failed search
    assert response.job_id in repo.outcomes  # failure is persisted, not lost


async def test_no_search_results_completes_with_nothing_to_do() -> None:
    service, _, extractor, _ = build(search=FakeSearch([]))

    response = await service.run(ResearchRequest(query="q"))

    assert response.status == JobStatus.COMPLETED and response.sources == []
    assert stage(response, ResearchStage.EXTRACTION).status == StageStatus.SKIPPED
    assert extractor.calls == []


async def test_all_urls_rejected_fails_and_records_each_rejection() -> None:
    results = [
        result(1, "http://127.0.0.1/"),
        result(2, "http://169.254.169.254/x"),
        result(3, "file:///etc/passwd"),
    ]
    service, _, extractor, _ = build(search=FakeSearch(results))

    response = await service.run(ResearchRequest(query="q"))

    assert response.status == JobStatus.FAILED
    assert response.failure.category == "no_eligible_urls"
    assert extractor.calls == []  # unsafe URLs never reach the extractor
    assert len(response.sources) == 3
    assert {s.extraction_status for s in response.sources} == {
        ExtractionStatus.REJECTED
    }
    assert {s.metadata["rejection_reason"] for s in response.sources} == {
        "blocked_ip",
        "unsupported_scheme",
    }


async def test_unsafe_urls_are_filtered_but_safe_ones_continue() -> None:
    results = [result(1, "http://10.0.0.1/"), result(2, "https://ok.example.com/")]
    service, _, extractor, _ = build(search=FakeSearch(results))

    response = await service.run(ResearchRequest(query="q"))

    assert response.status == JobStatus.COMPLETED
    assert extractor.calls == [["https://ok.example.com/"]]
    assert {s.extraction_status for s in response.sources} == {
        ExtractionStatus.REJECTED,
        ExtractionStatus.SUCCEEDED,
    }


async def test_duplicates_are_extracted_once() -> None:
    results = [
        result(1, "https://example.com/a"),
        result(2, "https://example.com/a#section"),
        result(3, "https://EXAMPLE.com/a"),
    ]
    service, _, extractor, _ = build(search=FakeSearch(results))

    response = await service.run(ResearchRequest(query="q"))

    assert extractor.calls == [["https://example.com/a"]]
    assert len(response.sources) == 1


async def test_page_budget_uses_the_smaller_of_request_and_settings() -> None:
    service, _, extractor, _ = build(research_max_pages=2)

    response = await service.run(ResearchRequest(query="q", max_results=10))

    assert len(extractor.calls[0]) == 2 and response.eligible_source_count == 2


async def test_scraping_disabled_skips_extraction() -> None:
    service, _, extractor, _ = build()

    response = await service.run(ResearchRequest(query="q", scrape_pages=False))

    assert response.status == JobStatus.COMPLETED and extractor.calls == []
    assert {s.extraction_status for s in response.sources} == {ExtractionStatus.SKIPPED}
    assert all(s.extracted_markdown is None for s in response.sources)
    assert stage(response, ResearchStage.EXTRACTION).status == StageStatus.SKIPPED


async def test_domain_allowlist_rejects_other_domains() -> None:
    service, _, extractor, _ = build()

    response = await service.run(
        ResearchRequest(query="q", allowed_domains=["site1.example.com"])
    )

    assert extractor.calls == [[URLS[0]]]
    rejected = [
        s for s in response.sources if s.extraction_status == ExtractionStatus.REJECTED
    ]
    assert {s.metadata["rejection_reason"] for s in rejected} == {"domain_not_allowed"}


async def test_verified_final_url_is_stored() -> None:
    page = ok_page(URLS[0], provider_final_url="https://final.example.com/landing#x")
    service, *_ = build(
        search=FakeSearch([result(1, URLS[0])]),
        extractor=FakeExtractor({URLS[0]: page}),
    )

    response = await service.run(ResearchRequest(query="q"))

    assert response.sources[0].final_url == "https://final.example.com/landing"


async def test_final_url_on_private_host_discards_content() -> None:
    page = ok_page(URLS[0], provider_final_url="https://intranet.example.com/secret")
    service, *_ = build(
        search=FakeSearch([result(1, URLS[0])]),
        extractor=FakeExtractor({URLS[0]: page}),
        policy=make_policy({"intranet.example.com": ["10.9.9.9"]}),
    )

    response = await service.run(ResearchRequest(query="q"))

    source = response.sources[0]
    assert source.extraction_status == ExtractionStatus.REJECTED
    assert source.extracted_markdown is None and source.final_url is None
    assert response.status == JobStatus.FAILED


async def test_unexpected_exception_is_contained_and_generic() -> None:
    service, _, _, repo = build(search=FakeSearch(error=RuntimeError("secret detail")))

    response = await service.run(ResearchRequest(query="q"))

    assert response.status == JobStatus.FAILED
    assert response.failure.category == "internal_error"
    assert "secret detail" not in response.model_dump_json()
    assert response.job_id in repo.outcomes


async def test_deadline_marks_job_failed() -> None:
    service, *_ = build(
        extractor=FakeExtractor(delay=1.0), research_timeout_seconds=0.05
    )

    response = await service.run(ResearchRequest(query="q"))

    assert response.status == JobStatus.FAILED
    assert response.failure.category == "deadline_exceeded"
    assert response.failure.category in INFRASTRUCTURE_CATEGORIES


async def test_cancellation_persists_a_failed_job_and_propagates() -> None:
    service, _, _, repo = build(extractor=FakeExtractor(delay=5))

    task = asyncio.create_task(service.run(ResearchRequest(query="q")))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    (job,) = repo.jobs.values()
    assert job.status == JobStatus.FAILED and job.failure.category == "cancelled"
    assert job.job_id in repo.outcomes


async def test_database_unavailable_at_start_raises() -> None:
    repo = InMemoryRepository()
    repo.fail_create = persistence_down()
    service, search, _, _ = build(repo=repo)

    with pytest.raises(PersistenceException):
        await service.run(ResearchRequest(query="q"))

    assert search.calls == []  # no provider credits spent without a job record


async def test_database_failure_on_save_raises() -> None:
    repo = InMemoryRepository()
    repo.fail_save = persistence_down()
    service, *_ = build(repo=repo)

    with pytest.raises(PersistenceException):
        await service.run(ResearchRequest(query="q"))


async def test_idempotent_request_id_returns_the_same_job() -> None:
    service, search, extractor, _ = build()
    request = ResearchRequest(query="q", request_id="idem-key-0001")

    first = await service.run(request)
    second = await service.run(request)

    assert second.job_id == first.job_id
    assert len(search.calls) == 1 and len(extractor.calls) == 1


async def test_get_missing_job_raises_not_found() -> None:
    from uuid import uuid4

    service, *_ = build()

    with pytest.raises(ResourceNotFoundException):
        await service.get(uuid4())


async def test_state_transitions_and_stage_logging(caplog) -> None:
    caplog.set_level(logging.INFO)
    service, _, _, repo = build()

    response = await service.run(ResearchRequest(query="private query text"))

    assert repo.jobs[response.job_id].status == JobStatus.COMPLETED
    text = caplog.text
    assert "research_started" in text and "research_stage" in text
    assert f"research_job_id={response.job_id}" in text and "stage=search" in text
    assert "private query text" not in text  # queries/content are not logged
