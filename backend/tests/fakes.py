"""Test doubles for providers, DNS, and persistence (no network, no database)."""

import asyncio
from uuid import UUID

from app.core.exceptions import PersistenceException
from app.database.repositories.research_repository import IdempotencyConflict
from app.research.url_policy import UrlPolicy
from app.schemas.research import (
    ErrorDetail,
    ExtractedPage,
    ExtractionStatus,
    ResearchJob,
    ResearchRequest,
    ResearchResponse,
    ResearchSource,
    SearchResult,
    StageSummary,
)

PUBLIC_IP = "93.184.216.34"


def make_resolver(mapping: dict[str, list[str]] | None = None, default=PUBLIC_IP):
    """DNS stub: ``mapping`` overrides; unknown hosts resolve to a public IP.

    A mapping value of ``OSError`` simulates a resolution failure.
    """
    table = mapping or {}

    async def resolve(host: str) -> list[str]:
        value = table.get(host, [default])
        if value is OSError:
            raise OSError("nxdomain")
        return value

    return resolve


def make_policy(mapping: dict[str, list[str]] | None = None, **kwargs) -> UrlPolicy:
    """A ``UrlPolicy`` using the DNS stub."""
    return UrlPolicy(resolver=make_resolver(mapping), **kwargs)


def result(position: int, url: str, title: str = "T") -> SearchResult:
    """Build a search result."""
    return SearchResult(position=position, title=f"{title}{position}", url=url)


def ok_page(url: str, markdown: str = "# Hello\n\ncontent", **meta) -> ExtractedPage:
    """A successful extraction for ``url``."""
    from app.core.hashing import content_hash

    return ExtractedPage(
        original_url=url,
        title="Page",
        markdown=markdown,
        status=ExtractionStatus.SUCCEEDED,
        content_hash=content_hash(markdown),
        metadata=dict(meta),
    )


def failed_page(url: str, category: str = "provider_unavailable") -> ExtractedPage:
    """A failed extraction for ``url``."""
    return ExtractedPage(
        original_url=url,
        status=ExtractionStatus.FAILED,
        error_message="boom",
        metadata={"error_category": category},
    )


class FakeSearch:
    """Search provider returning canned results or raising."""

    def __init__(self, results=None, error: Exception | None = None) -> None:
        self.results = results or []
        self.error = error
        self.calls: list[dict] = []

    async def search(self, query, *, num_results=10, country=None, language=None):
        self.calls.append(
            {
                "query": query,
                "num": num_results,
                "country": country,
                "language": language,
            }
        )
        if self.error:
            raise self.error
        return list(self.results)


class FakeExtractor:
    """Extractor returning a page per URL via ``pages`` (url -> page) or default OK."""

    def __init__(self, pages=None, error: Exception | None = None, delay=0.0) -> None:
        self.pages = pages or {}
        self.error = error
        self.delay = delay
        self.calls: list[list[str]] = []
        self.concurrency: int | None = None

    async def scrape_many(self, urls, *, concurrency=None):
        self.calls.append(list(urls))
        self.concurrency = concurrency
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return [self.pages.get(u) or ok_page(u) for u in urls]


class InMemoryRepository:
    """JobRepository implementation backed by dicts."""

    def __init__(self) -> None:
        self.jobs: dict[UUID, ResearchJob] = {}
        self.requests: dict[UUID, ResearchRequest] = {}
        self.outcomes: dict[UUID, ResearchResponse] = {}
        self.fail_create: Exception | None = None
        self.fail_save: Exception | None = None

    async def create_job(self, job: ResearchJob, request: ResearchRequest) -> None:
        if self.fail_create:
            raise self.fail_create
        if request.request_id and any(
            r.request_id == request.request_id for r in self.requests.values()
        ):
            raise IdempotencyConflict
        self.jobs[job.job_id] = job
        self.requests[job.job_id] = request

    async def find_job_id_by_request_id(self, request_id: str) -> UUID | None:
        for job_id, request in self.requests.items():
            if request.request_id == request_id:
                return job_id
        return None

    async def save_outcome(
        self,
        job: ResearchJob,
        *,
        search_results: list[SearchResult],
        sources: list[ResearchSource],
        stages: list[StageSummary],
        errors: list[ErrorDetail],
    ) -> None:
        if self.fail_save:
            raise self.fail_save
        self.jobs[job.job_id] = job
        self.outcomes[job.job_id] = ResearchResponse(
            job_id=job.job_id,
            query=job.query,
            status=job.status,
            requested_result_count=job.requested_result_count,
            actual_result_count=job.actual_result_count,
            eligible_source_count=job.eligible_source_count,
            successful_source_count=job.successful_source_count,
            failed_source_count=job.failed_source_count,
            search_results=search_results,
            sources=sources,
            stages=stages,
            errors=errors,
            failure=job.failure,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )

    async def get_response(self, job_id: UUID, *, include_content: bool = True):
        stored = self.outcomes.get(job_id)
        if stored is None:
            return None
        if include_content:
            return stored
        copy = stored.model_copy(deep=True)
        for source in copy.sources:
            source.extracted_markdown = None
        return copy


def persistence_down() -> PersistenceException:
    """A safe persistence error, as the repository would raise."""
    return PersistenceException("The database is unavailable.")
