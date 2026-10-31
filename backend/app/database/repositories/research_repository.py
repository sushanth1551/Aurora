"""Persistence for research jobs, search results, and sources.

All SQL lives here (never in routes). Methods commit their own unit of work and
translate database/driver failures into ``PersistenceException`` with a safe
message (no connection strings, no SQL text).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import PersistenceException
from app.database.models import (
    ResearchJobRecord,
    ResearchSourceRecord,
    SearchResultRecord,
)
from app.schemas.research import (
    ErrorDetail,
    ExtractionStatus,
    JobStatus,
    ResearchJob,
    ResearchRequest,
    ResearchResponse,
    ResearchSource,
    SearchResult,
    StageSummary,
    utc_now,
)

logger = logging.getLogger(__name__)


def _coerce_status(value: str) -> JobStatus:
    """Map any stored status to a Module 2 ``JobStatus``.

    Website-intelligence jobs share the table and have extra in-progress
    statuses; those are reported as ``running``.
    """
    try:
        return JobStatus(value)
    except ValueError:
        return JobStatus.RUNNING


def _stages_from(raw: list[dict] | None) -> list[StageSummary]:
    """Parse stored stage summaries, skipping entries this API cannot represent.

    Website-intelligence jobs store their own stage names (e.g. ``discovery``).
    """
    stages: list[StageSummary] = []
    for item in raw or []:
        try:
            stages.append(StageSummary.model_validate(item))
        except ValidationError:
            continue
    return stages


class IdempotencyConflict(Exception):
    """A job with the same ``request_id`` already exists."""


@asynccontextmanager
async def _translate_errors(session: AsyncSession) -> AsyncIterator[None]:
    try:
        yield
    except IdempotencyConflict:
        raise
    except (SQLAlchemyError, OSError) as exc:
        await session.rollback()
        logger.error("database_error type=%s", type(exc).__name__)
        raise PersistenceException("The database is unavailable.") from exc


class ResearchRepository:
    """Store and retrieve research jobs using an injected async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_job(self, job: ResearchJob, request: ResearchRequest) -> None:
        """Insert the job row (status as given) and commit."""
        record = ResearchJobRecord(
            id=job.job_id,
            request_id=request.request_id,
            query=job.query,
            status=job.status.value,
            max_results=request.max_results,
            scrape_pages=request.scrape_pages,
            country=request.country,
            language=request.language,
            allowed_domains=request.allowed_domains,
            created_at=job.created_at,
            started_at=job.started_at,
            errors=[],
            stage_summaries=[],
        )
        self._session.add(record)
        try:
            async with _translate_errors(self._session):
                await self._session.commit()
        except PersistenceException as exc:
            if request.request_id and isinstance(exc.__cause__, IntegrityError):
                raise IdempotencyConflict from exc
            raise

    async def find_job_id_by_request_id(self, request_id: str) -> UUID | None:
        """Return the id of the job created with this idempotency key, if any."""
        async with _translate_errors(self._session):
            result = await self._session.execute(
                select(ResearchJobRecord.id).where(
                    ResearchJobRecord.request_id == request_id
                )
            )
            return result.scalar_one_or_none()

    async def save_outcome(
        self,
        job: ResearchJob,
        *,
        search_results: list[SearchResult],
        sources: list[ResearchSource],
        stages: list[StageSummary],
        errors: list[ErrorDetail],
    ) -> None:
        """Atomically store final job state, search results, and sources."""
        async with _translate_errors(self._session):
            record = await self._session.get(ResearchJobRecord, job.job_id)
            if record is None:
                raise PersistenceException("Research job record is missing.")
            record.status = job.status.value
            record.total_search_results = job.actual_result_count
            record.eligible_source_count = job.eligible_source_count
            record.successful_source_count = job.successful_source_count
            record.failed_source_count = job.failed_source_count
            record.completed_at = job.completed_at
            record.error_category = job.failure.category if job.failure else None
            record.error_summary = job.failure.message if job.failure else None
            record.errors = [e.model_dump(mode="json") for e in errors]
            record.stage_summaries = [s.model_dump(mode="json") for s in stages]
            now = utc_now()
            for result in search_results:
                self._session.add(
                    SearchResultRecord(
                        research_job_id=job.job_id,
                        position=result.position,
                        title=result.title,
                        url=result.url,
                        snippet=result.snippet,
                        displayed_link=result.displayed_link,
                        domain=result.domain,
                        provider_metadata=result.metadata,
                        created_at=now,
                    )
                )
            for source in sources:
                self._session.add(
                    ResearchSourceRecord(
                        id=source.id,
                        research_job_id=job.job_id,
                        original_url=source.original_url,
                        normalized_url=source.normalized_url,
                        final_url=source.final_url,
                        domain=source.domain,
                        title=source.title,
                        snippet=source.snippet,
                        extracted_markdown=source.extracted_markdown,
                        extraction_status=source.extraction_status.value,
                        content_hash=source.content_hash,
                        provider_metadata=source.metadata,
                        error_message=source.error_message,
                        created_at=source.created_at,
                        extracted_at=source.extracted_at,
                    )
                )
            await self._session.commit()

    async def get_response(
        self, job_id: UUID, *, include_content: bool = True
    ) -> ResearchResponse | None:
        """Load a job with its search results and sources, or ``None``."""
        async with _translate_errors(self._session):
            result = await self._session.execute(
                select(ResearchJobRecord)
                .where(ResearchJobRecord.id == job_id)
                .options(
                    selectinload(ResearchJobRecord.search_results),
                    selectinload(ResearchJobRecord.sources),
                )
                .execution_options(populate_existing=True)
            )
            record = result.scalar_one_or_none()
        if record is None:
            return None
        return self._to_response(record, include_content)

    @staticmethod
    def _to_response(
        record: ResearchJobRecord, include_content: bool
    ) -> ResearchResponse:
        return ResearchResponse(
            job_id=record.id,
            query=record.query,
            status=_coerce_status(record.status),
            requested_result_count=record.max_results,
            actual_result_count=record.total_search_results,
            eligible_source_count=record.eligible_source_count,
            successful_source_count=record.successful_source_count,
            failed_source_count=record.failed_source_count,
            search_results=[
                SearchResult(
                    position=r.position,
                    title=r.title,
                    url=r.url,
                    snippet=r.snippet,
                    displayed_link=r.displayed_link,
                    domain=r.domain,
                    metadata=r.provider_metadata or {},
                )
                for r in record.search_results
            ],
            sources=[
                ResearchSource(
                    id=s.id,
                    original_url=s.original_url,
                    normalized_url=s.normalized_url,
                    final_url=s.final_url,
                    domain=s.domain,
                    title=s.title,
                    snippet=s.snippet,
                    extracted_markdown=s.extracted_markdown
                    if include_content
                    else None,
                    extraction_status=ExtractionStatus(s.extraction_status),
                    content_hash=s.content_hash,
                    metadata=s.provider_metadata or {},
                    error_message=s.error_message,
                    created_at=s.created_at,
                    extracted_at=s.extracted_at,
                )
                for s in record.sources
            ],
            stages=_stages_from(record.stage_summaries),
            errors=[ErrorDetail.model_validate(e) for e in record.errors or []],
            failure=(
                ErrorDetail(
                    category=record.error_category, message=record.error_summary or ""
                )
                if record.error_category
                else None
            ),
            created_at=record.created_at,
            completed_at=record.completed_at,
        )
