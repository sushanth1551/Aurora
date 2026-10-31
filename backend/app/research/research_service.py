"""Research service: search -> validate -> extract -> persist.

Runs a research job *synchronously* inside the request (no background queue):
the caller waits until the job finishes or ``research_timeout_seconds`` passes.
It uses no LLM, only provider data, and is independent of FastAPI. Providers,
the source manager and the repository are injected, so tests mock them and a
future LangGraph graph can wrap each stage as a node (see ``docs/research-engine.md``).

Status rules
------------
* provider/search failure, deadline, cancellation, internal error -> ``failed``
* search found nothing -> ``completed`` with zero sources
* every result rejected/duplicate -> ``failed`` (``no_eligible_urls``)
* scraping disabled -> ``completed`` (sources recorded as ``skipped``)
* scraping on: all pages failed -> ``failed``; some failed -> ``partially_completed``
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ConfigurationException,
    PersistenceException,
    ResourceNotFoundException,
)
from app.core.logging import log_event
from app.database.repositories.research_repository import IdempotencyConflict
from app.research.source_manager import PreparedSources, SourceCandidate, SourceManager
from app.research.url_policy import UrlRejected
from app.schemas.research import (
    ErrorDetail,
    ExtractedPage,
    ExtractionStatus,
    JobStatus,
    ResearchJob,
    ResearchRequest,
    ResearchResponse,
    ResearchSource,
    ResearchStage,
    SearchResult,
    StageStatus,
    StageSummary,
    utc_now,
)
from app.tools.errors import ProviderError
from app.tools.http import build_timeout

logger = logging.getLogger(__name__)

#: Failure categories meaning an essential service is unavailable (HTTP 503).
INFRASTRUCTURE_CATEGORIES = frozenset(
    {
        "provider_not_configured",
        "provider_authentication_error",
        "provider_rate_limited",
        "provider_unavailable",
        "provider_timeout",
        "provider_circuit_open",
        "provider_budget_exceeded",
        "persistence_error",
        "deadline_exceeded",
    }
)


class SearchProvider(Protocol):
    """Search adapter contract (``SerpApiTool``)."""

    async def search(
        self,
        query: str,
        *,
        num_results: int = 10,
        country: str | None = None,
        language: str | None = None,
    ) -> list[SearchResult]: ...


class PageExtractor(Protocol):
    """Extraction adapter contract (``FirecrawlTool``)."""

    async def scrape_many(
        self, urls: list[str], *, concurrency: int | None = None
    ) -> list[ExtractedPage]: ...


class JobRepository(Protocol):
    """Persistence contract (``ResearchRepository``)."""

    async def create_job(self, job: ResearchJob, request: ResearchRequest) -> None: ...

    async def find_job_id_by_request_id(self, request_id: str) -> UUID | None: ...

    async def save_outcome(
        self,
        job: ResearchJob,
        *,
        search_results: list[SearchResult],
        sources: list[ResearchSource],
        stages: list[StageSummary],
        errors: list[ErrorDetail],
    ) -> None: ...

    async def get_response(
        self, job_id: UUID, *, include_content: bool = True
    ) -> ResearchResponse | None: ...


class StageFailure(Exception):
    """A stage cannot continue; carries a client-safe category and message."""

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        self.message = message
        super().__init__(message)


class _Run:
    """Mutable bookkeeping for one job execution."""

    def __init__(self, job: ResearchJob) -> None:
        self.job = job
        self.stage = ResearchStage.SEARCH
        self.results: list[SearchResult] = []
        self.sources: list[ResearchSource] = []
        self.stages: list[StageSummary] = []
        self.errors: list[ErrorDetail] = []
        self.failure: ErrorDetail | None = None
        self.selected = 0
        self.succeeded = 0
        self.failed = 0
        self.extraction_categories: list[str] = []


def classify_failure(exc: Exception) -> StageFailure | None:
    """Map expected provider/config errors to a ``StageFailure`` (else ``None``).

    Provider errors keep their own stable ``code`` (e.g. ``provider_rate_limited``).
    """
    if isinstance(exc, ConfigurationException):
        return StageFailure("provider_not_configured", exc.message)
    if isinstance(exc, ProviderError):
        return StageFailure(exc.code, exc.message)
    return None


class ResearchService:
    """Coordinate adapters, source policy and persistence for research jobs."""

    def __init__(
        self,
        *,
        search: SearchProvider,
        extractor: PageExtractor,
        sources: SourceManager,
        repository: JobRepository,
        settings: Settings | None = None,
    ) -> None:
        self._search = search
        self._extractor = extractor
        self._sources = sources
        self._repo = repository
        self._settings = settings or get_settings()

    # -- public API -----------------------------------------------------------

    async def run(self, request: ResearchRequest) -> ResearchResponse:
        """Execute a research job and return the persisted result."""
        if request.request_id:
            existing = await self._repo.find_job_id_by_request_id(request.request_id)
            if existing is not None:
                return await self.get(existing)
        job = ResearchJob(
            query=request.query,
            status=JobStatus.RUNNING,
            requested_result_count=request.max_results,
            started_at=utc_now(),
            request_id=request.request_id,
        )
        try:
            await self._repo.create_job(job, request)
        except IdempotencyConflict:
            assert request.request_id is not None
            existing = await self._repo.find_job_id_by_request_id(request.request_id)
            if existing is None:
                raise PersistenceException(
                    "Idempotent job could not be found."
                ) from None
            return await self.get(existing)

        run = _Run(job)
        log_event(logger, "research_started", research_job_id=job.job_id)
        try:
            async with asyncio.timeout(self._settings.research_timeout_seconds):
                await self._execute(request, run)
        except StageFailure as failure:
            run.failure = ErrorDetail(
                stage=run.stage, category=failure.category, message=failure.message
            )
        except TimeoutError:
            run.failure = ErrorDetail(
                stage=run.stage,
                category="deadline_exceeded",
                message="The research deadline was exceeded.",
            )
        except asyncio.CancelledError:
            run.failure = ErrorDetail(
                stage=run.stage, category="cancelled", message="The job was cancelled."
            )
            await asyncio.shield(self._finalize_quietly(run))
            raise
        except PersistenceException:
            raise
        except Exception:
            logger.exception("research_internal_error research_job_id=%s", job.job_id)
            run.failure = ErrorDetail(
                stage=run.stage,
                category="internal_error",
                message="An unexpected error occurred.",
            )
        return await self._finalize(run)

    async def get(
        self, job_id: UUID, *, include_content: bool = True
    ) -> ResearchResponse:
        """Return a stored job or raise ``ResourceNotFoundException``."""
        response = await self._repo.get_response(
            job_id, include_content=include_content
        )
        if response is None:
            raise ResourceNotFoundException("Research job not found.")
        return response

    # -- pipeline -------------------------------------------------------------

    async def _execute(self, request: ResearchRequest, run: _Run) -> None:
        await self._stage(
            run, ResearchStage.SEARCH, lambda: self._do_search(request, run)
        )
        if not run.results:
            for stage in (ResearchStage.VALIDATION, ResearchStage.EXTRACTION):
                self._record(run, stage, StageStatus.SKIPPED, utc_now(), 0, 0)
            return
        prepared: PreparedSources = PreparedSources()

        async def validate() -> int:
            nonlocal prepared
            prepared = await self._do_validate(request, run)
            return len(prepared.selected) + len(prepared.rejected)

        await self._stage(run, ResearchStage.VALIDATION, validate)
        run.selected = len(prepared.selected)
        run.sources.extend(self._sources.rejected_source(c) for c in prepared.rejected)
        if not request.scrape_pages or not prepared.selected:
            self._record(
                run, ResearchStage.EXTRACTION, StageStatus.SKIPPED, utc_now(), 0, 0
            )
            if not request.scrape_pages:
                run.sources.extend(
                    self._sources.build_source(c, ExtractionStatus.SKIPPED)
                    for c in prepared.selected
                )
            return
        await self._do_extract(request, run, prepared.selected)

    async def _do_search(self, request: ResearchRequest, run: _Run) -> int:
        run.results = await self._search.search(
            request.query,
            num_results=request.max_results,
            country=request.country,
            language=request.language,
        )
        run.job.actual_result_count = len(run.results)
        return len(run.results)

    async def _do_validate(
        self, request: ResearchRequest, run: _Run
    ) -> PreparedSources:
        budget = request.max_results
        if request.scrape_pages:
            budget = min(budget, self._settings.research_max_pages)
        prepared = await self._sources.prepare(
            run.results, max_sources=budget, allowed_domains=request.allowed_domains
        )
        if self._settings.research_preflight_redirects and prepared.selected:
            await self._preflight(request, prepared)
        run.job.eligible_source_count = len(prepared.selected)
        return prepared

    async def _preflight(
        self, request: ResearchRequest, prepared: PreparedSources
    ) -> None:
        """Optionally trace redirects (HEAD, manual, revalidated per hop)."""
        timeout = build_timeout(
            self._settings.research_http_connect_timeout_seconds,
            self._settings.research_http_read_timeout_seconds,
        )
        kept: list[SourceCandidate] = []
        async with httpx.AsyncClient(timeout=timeout) as client:
            for candidate in prepared.selected:
                assert candidate.validated is not None
                try:
                    await self._sources.policy.trace_redirects(
                        candidate.validated.normalized, client, request.allowed_domains
                    )
                except UrlRejected as exc:
                    prepared.rejected.append(
                        SourceCandidate(candidate.result, rejection=exc)
                    )
                else:
                    kept.append(candidate)
        prepared.selected = kept

    async def _do_extract(
        self, request: ResearchRequest, run: _Run, selected: list[SourceCandidate]
    ) -> None:
        started = utc_now()
        t0 = time.monotonic()
        run.stage = ResearchStage.EXTRACTION
        urls = [c.validated.normalized for c in selected if c.validated]
        try:
            pages = await self._extractor.scrape_many(
                urls, concurrency=self._settings.research_max_concurrent_extractions
            )
        except Exception as exc:
            failure = classify_failure(exc)
            if failure is None:
                raise
            pages = [
                ExtractedPage(
                    original_url=u,
                    status=ExtractionStatus.FAILED,
                    error_message=failure.message,
                    metadata={"error_category": failure.category},
                )
                for u in urls
            ]
        for candidate, page in zip(selected, pages, strict=True):
            run.sources.append(await self._source_from_page(request, candidate, page))
        run.succeeded = sum(
            s.extraction_status == ExtractionStatus.SUCCEEDED for s in run.sources
        )
        extraction_failures = [
            s for s in run.sources if s.metadata.get("stage") == "extraction"
        ]
        run.failed = len(extraction_failures)
        run.extraction_categories = [
            str(s.metadata.get("error_category")) for s in extraction_failures
        ]
        error: ErrorDetail | None = None
        status = StageStatus.SUCCEEDED
        if run.succeeded == 0:
            status = StageStatus.FAILED
            category = self._dominant_category(run.extraction_categories)
            error = ErrorDetail(
                stage=ResearchStage.EXTRACTION,
                category=category,
                message="No page could be extracted.",
            )
        elif run.failed:
            error = ErrorDetail(
                stage=ResearchStage.EXTRACTION,
                category="partial_extraction",
                message=f"{run.failed} of {run.selected} pages could not be extracted.",
            )
        self._record(
            run,
            ResearchStage.EXTRACTION,
            status,
            started,
            int((time.monotonic() - t0) * 1000),
            run.succeeded,
            error,
        )
        if error is not None:
            run.errors.append(error)

    async def _source_from_page(
        self, request: ResearchRequest, candidate: SourceCandidate, page: ExtractedPage
    ) -> ResearchSource:
        if page.status != ExtractionStatus.SUCCEEDED:
            category = str(page.metadata.get("error_category", "extraction_failed"))
            source = self._sources.build_source(
                candidate,
                ExtractionStatus.FAILED,
                page=page,
                error=page.error_message or "extraction failed",
            )
            source.metadata.update(stage="extraction", error_category=category)
            return source
        try:
            final_url = await self._sources.verify_final_url(
                page, request.allowed_domains
            )
        except UrlRejected as exc:
            source = self._sources.build_source(
                candidate,
                ExtractionStatus.REJECTED,
                error=f"Final URL rejected: {exc.message}",
            )
            source.metadata.update(
                stage="extraction",
                error_category="final_url_rejected",
                rejection_reason=exc.reason,
            )
            return source
        return self._sources.build_source(
            candidate, ExtractionStatus.SUCCEEDED, page=page, final_url=final_url
        )

    # -- helpers --------------------------------------------------------------

    async def _stage(
        self, run: _Run, stage: ResearchStage, func: Callable[[], Awaitable[int]]
    ) -> None:
        """Run a stage; record its summary; turn expected errors into StageFailure."""
        started = utc_now()
        t0 = time.monotonic()
        run.stage = stage
        try:
            count = await func()
        except StageFailure as failure:
            self._record_failure(run, stage, started, t0, failure)
            raise
        except Exception as exc:
            classified = classify_failure(exc)
            if classified is None:
                raise
            self._record_failure(run, stage, started, t0, classified)
            raise classified from exc
        self._record(
            run,
            stage,
            StageStatus.SUCCEEDED,
            started,
            int((time.monotonic() - t0) * 1000),
            count,
        )

    def _record_failure(
        self,
        run: _Run,
        stage: ResearchStage,
        started: datetime,
        t0: float,
        failure: StageFailure,
    ) -> None:
        error = ErrorDetail(
            stage=stage, category=failure.category, message=failure.message
        )
        self._record(
            run,
            stage,
            StageStatus.FAILED,
            started,
            int((time.monotonic() - t0) * 1000),
            None,
            error,
        )

    def _record(
        self,
        run: _Run,
        stage: ResearchStage,
        status: StageStatus,
        started: datetime,
        duration_ms: int,
        count: int | None,
        error: ErrorDetail | None = None,
    ) -> None:
        run.stages.append(
            StageSummary(
                stage=stage,
                status=status,
                started_at=started,
                duration_ms=duration_ms,
                item_count=count,
                error=error,
            )
        )
        log_event(
            logger,
            "research_stage",
            level=logging.WARNING if status == StageStatus.FAILED else logging.INFO,
            research_job_id=run.job.job_id,
            stage=stage.value,
            status=status.value,
            duration_ms=duration_ms,
            error_category=error.category if error else "-",
        )

    @staticmethod
    def _dominant_category(categories: list[str]) -> str:
        unique = set(categories)
        return unique.pop() if len(unique) == 1 else "all_extractions_failed"

    # -- finalization ---------------------------------------------------------

    async def _finalize(self, run: _Run) -> ResearchResponse:
        job = run.job
        job.completed_at = utc_now()
        job.successful_source_count = run.succeeded
        job.failed_source_count = run.failed
        self._decide_status(run)
        started = utc_now()
        t0 = time.monotonic()
        try:
            await self._repo.save_outcome(
                job,
                search_results=run.results,
                sources=run.sources,
                stages=run.stages,
                errors=run.errors,
            )
        except PersistenceException:
            log_event(
                logger,
                "research_stage",
                level=logging.ERROR,
                research_job_id=job.job_id,
                stage="persistence",
                status="failed",
                error_category="persistence_error",
            )
            raise
        self._record(
            run,
            ResearchStage.PERSISTENCE,
            StageStatus.SUCCEEDED,
            started,
            int((time.monotonic() - t0) * 1000),
            len(run.sources),
        )
        log_event(
            logger,
            "research_finished",
            research_job_id=job.job_id,
            status=job.status.value,
            sources_ok=run.succeeded,
            sources_failed=run.failed,
        )
        return ResearchResponse(
            job_id=job.job_id,
            query=job.query,
            status=job.status,
            requested_result_count=job.requested_result_count,
            actual_result_count=job.actual_result_count,
            eligible_source_count=job.eligible_source_count,
            successful_source_count=job.successful_source_count,
            failed_source_count=job.failed_source_count,
            search_results=run.results,
            sources=run.sources,
            stages=run.stages,
            errors=run.errors,
            failure=job.failure,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )

    def _decide_status(self, run: _Run) -> None:
        job = run.job
        failure = run.failure
        if failure is None and run.results and run.selected == 0:
            failure = ErrorDetail(
                stage=ResearchStage.VALIDATION,
                category="no_eligible_urls",
                message="Every search result was rejected or duplicated.",
            )
        extraction_on = any(
            s.stage == ResearchStage.EXTRACTION and s.status != StageStatus.SKIPPED
            for s in run.stages
        )
        if failure is None and extraction_on and run.succeeded == 0 and run.errors:
            failure = run.errors[-1]
        if failure is not None:
            job.status = JobStatus.FAILED
            job.failure = failure
            if failure not in run.errors:
                run.errors.append(failure)
        elif run.failed:
            job.status = JobStatus.PARTIALLY_COMPLETED
        else:
            job.status = JobStatus.COMPLETED

    async def _finalize_quietly(self, run: _Run) -> None:
        """Best-effort persistence while being cancelled; never raises."""
        try:
            await self._finalize(run)
        except Exception:
            logger.warning(
                "research_cancel_persist_failed research_job_id=%s", run.job.job_id
            )
