"""Schemas for the research engine: requests, provider-neutral results, responses.

Request/response schemas (this module), persistence models
(``app.database.models``) and the internal workflow state
(``app.research.state``) are deliberately separate. Nothing here holds
credentials.
"""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

_COUNTRY = re.compile(r"^[a-z]{2}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z0-9-]{2,63}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def utc_now() -> datetime:
    """Timezone-aware current time."""
    return datetime.now(UTC)


class JobStatus(StrEnum):
    """Lifecycle of a research job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"


class ExtractionStatus(StrEnum):
    """Outcome of processing a single source."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"  # blocked by URL policy; never fetched
    SKIPPED = "skipped"  # not fetched (scraping disabled or page budget reached)


class ResearchStage(StrEnum):
    """Pipeline stages, used for stage-level observability."""

    SEARCH = "search"
    VALIDATION = "validation"
    EXTRACTION = "extraction"
    PERSISTENCE = "persistence"


class StageStatus(StrEnum):
    """Outcome of a pipeline stage."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ResearchRequest(BaseModel):
    """Input for a research job."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(min_length=1, max_length=500, description="Research query")
    max_results: int = Field(default=10, ge=1, le=100, description="Search results")
    scrape_pages: bool = Field(default=True, description="Extract page content")
    country: str | None = Field(
        default=None, description="Two-letter market code, e.g. 'in'"
    )
    language: str | None = Field(default=None, description="Language code, e.g. 'en'")
    allowed_domains: list[str] | None = Field(
        default=None,
        max_length=50,
        description="If set, only these domains (and subdomains) are processed",
    )
    request_id: str | None = Field(
        default=None,
        description="Optional idempotency key; repeating it returns the same job",
    )

    @field_validator("country")
    @classmethod
    def _check_country(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.lower()
        if not _COUNTRY.fullmatch(value):
            raise ValueError("country must be a two-letter code")
        return value

    @field_validator("language")
    @classmethod
    def _check_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.lower()
        if not _LANGUAGE.fullmatch(value):
            raise ValueError("language must look like 'en' or 'pt-br'")
        return value

    @field_validator("allowed_domains")
    @classmethod
    def _check_domains(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [item.strip().lower().rstrip(".") for item in value]
        for item in cleaned:
            if not _DOMAIN.fullmatch(item):
                raise ValueError("allowed_domains must be bare domain names")
        return sorted(set(cleaned))

    @field_validator("request_id")
    @classmethod
    def _check_request_id(cls, value: str | None) -> str | None:
        if value is not None and not _IDEMPOTENCY_KEY.fullmatch(value):
            raise ValueError("request_id must be 8-128 characters of [A-Za-z0-9._:-]")
        return value


class SearchResult(BaseModel):
    """One normalized organic search result (provider-neutral)."""

    position: int = Field(ge=1)
    title: str = Field(default="", max_length=1000)
    url: str = Field(max_length=2048)
    snippet: str | None = Field(default=None, max_length=5000)
    displayed_link: str | None = Field(default=None, max_length=1000)
    domain: str | None = Field(default=None, max_length=253)
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Small, useful provider fields"
    )


class ExtractedPage(BaseModel):
    """Normalized scrape output for one page."""

    original_url: str = Field(max_length=2048)
    final_url: str | None = Field(
        default=None, description="Set only when verified against URL policy"
    )
    title: str | None = None
    description: str | None = None
    markdown: str = ""
    status: ExtractionStatus = ExtractionStatus.PENDING
    provider_status: int | None = Field(
        default=None, description="HTTP status of the target page, if reported"
    )
    extracted_at: datetime = Field(default_factory=utc_now)
    content_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


class ResearchSource(BaseModel):
    """A source with provenance; content never travels without its URL."""

    id: UUID = Field(default_factory=uuid4)
    original_url: str
    normalized_url: str | None = None
    final_url: str | None = None
    domain: str | None = None
    title: str | None = None
    snippet: str | None = None
    extracted_markdown: str | None = None
    extraction_status: ExtractionStatus = ExtractionStatus.PENDING
    content_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    extracted_at: datetime | None = None


class ErrorDetail(BaseModel):
    """Client-safe error description."""

    stage: ResearchStage | None = None
    category: str
    message: str = Field(max_length=1000)


class StageSummary(BaseModel):
    """What happened in one pipeline stage."""

    stage: ResearchStage
    status: StageStatus
    started_at: datetime
    duration_ms: int = Field(ge=0)
    item_count: int | None = None
    error: ErrorDetail | None = None


class ResearchJob(BaseModel):
    """Job-level record."""

    job_id: UUID = Field(default_factory=uuid4)
    query: str
    status: JobStatus = JobStatus.PENDING
    requested_result_count: int
    actual_result_count: int = 0
    eligible_source_count: int = 0
    successful_source_count: int = 0
    failed_source_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure: ErrorDetail | None = None
    request_id: str | None = None


class ResearchResponse(BaseModel):
    """Result of running or retrieving a research job."""

    job_id: UUID
    query: str
    status: JobStatus
    requested_result_count: int
    actual_result_count: int
    eligible_source_count: int
    successful_source_count: int
    failed_source_count: int
    search_results: list[SearchResult] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    stages: list[StageSummary] = Field(default_factory=list)
    errors: list[ErrorDetail] = Field(default_factory=list)
    failure: ErrorDetail | None = Field(
        default=None, description="Why the job failed, when status is 'failed'"
    )
    created_at: datetime
    completed_at: datetime | None = None


class SourceReference(BaseModel):
    """Lightweight provenance pointer used by SEO/content schemas."""

    url: HttpUrl = Field(description="Source location")
    title: str | None = Field(default=None, max_length=500)
    retrieved_at: datetime | None = Field(
        default=None, description="When the content was retrieved (UTC)"
    )
