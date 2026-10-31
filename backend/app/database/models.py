"""SQLAlchemy 2.x ORM models for research jobs and their sources.

Tables: ``research_jobs`` (one row per run), ``search_results`` (every organic
result returned by the search provider) and ``research_sources`` (every URL that
was selected or rejected, with provenance). Child rows reference their job with
a NOT NULL, ``ON DELETE CASCADE`` foreign key, so a source can never exist
without its job. No credentials are ever stored.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB on PostgreSQL, plain JSON elsewhere (e.g. SQLite in unit tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Declarative base class for all ORM models."""


# Backwards-compatible name used by the earlier scaffold.
BaseModel = Base


class ResearchJobRecord(Base):
    """One research run."""

    __tablename__ = "research_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    job_type: Mapped[str] = mapped_column(
        String(32), default="research", server_default="research"
    )
    query: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(32), index=True)
    max_results: Mapped[int] = mapped_column(Integer)
    scrape_pages: Mapped[bool] = mapped_column(Boolean)
    country: Mapped[str | None] = mapped_column(String(2))
    language: Mapped[str | None] = mapped_column(String(16))
    allowed_domains: Mapped[list[str] | None] = mapped_column(JSONType)
    total_search_results: Mapped[int] = mapped_column(Integer, default=0)
    eligible_source_count: Mapped[int] = mapped_column(Integer, default=0)
    successful_source_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_source_count: Mapped[int] = mapped_column(Integer, default=0)
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    stage_summaries: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, default=list
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    search_results: Mapped[list["SearchResultRecord"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="SearchResultRecord.position",
    )
    sources: Mapped[list["ResearchSourceRecord"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="ResearchSourceRecord.created_at",
    )


class SearchResultRecord(Base):
    """An organic search result as returned by the provider."""

    __tablename__ = "search_results"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    research_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    displayed_link: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(253), index=True)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    job: Mapped[ResearchJobRecord] = relationship(back_populates="search_results")


class ResearchSourceRecord(Base):
    """A source considered for extraction, with provenance and outcome."""

    __tablename__ = "research_sources"
    __table_args__ = (
        UniqueConstraint(
            "research_job_id", "normalized_url", name="uq_source_job_normalized_url"
        ),
        Index("ix_research_sources_normalized_url", "normalized_url"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    research_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True
    )
    original_url: Mapped[str] = mapped_column(Text)
    normalized_url: Mapped[str | None] = mapped_column(Text)
    final_url: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(253), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    extracted_markdown: Mapped[str | None] = mapped_column(Text)
    extraction_status: Mapped[str] = mapped_column(String(32), index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[ResearchJobRecord] = relationship(back_populates="sources")


# -- Module 3: website intelligence ---------------------------------------------


class ResearchWebsiteRecord(Base):
    """A website discovered for a research job."""

    __tablename__ = "research_websites"
    __table_args__ = (
        UniqueConstraint("research_job_id", "domain", name="uq_website_job_domain"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    research_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True
    )
    website_name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(253), index=True)
    homepage_url: Mapped[str] = mapped_column(Text)
    official_status: Mapped[str] = mapped_column(String(32))
    website_type: Mapped[str] = mapped_column(String(32))
    relevance_score: Mapped[float] = mapped_column(Float)
    discovery_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MappedPageRecord(Base):
    """A URL mapped within a website."""

    __tablename__ = "mapped_pages"
    __table_args__ = (
        UniqueConstraint("website_id", "normalized_url", name="uq_page_website_url"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    website_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_websites.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    normalized_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    page_type: Mapped[str] = mapped_column(String(32), index=True)
    relevance_score: Mapped[float] = mapped_column(Float)
    crawl_eligible: Mapped[bool] = mapped_column(Boolean)
    crawl_status: Mapped[str] = mapped_column(String(32), default="pending")
    exclusion_reason: Mapped[str | None] = mapped_column(String(100))
    depth: Mapped[int] = mapped_column(Integer, default=0)
    discovery_method: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProcessedDocumentRecord(Base):
    """Normalized page content; the original lives on the linked source."""

    __tablename__ = "processed_documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    research_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_sources.id", ondelete="CASCADE"), index=True
    )
    page_type: Mapped[str] = mapped_column(String(32))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    original_hash: Mapped[str | None] = mapped_column(String(64))
    normalized_content: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    document_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    published_at: Mapped[str | None] = mapped_column(String(64))
    modified_at: Mapped[str | None] = mapped_column(String(64))
    processing_status: Mapped[str] = mapped_column(String(32))
    duplicate_of: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchEntityRecord(Base):
    """A resolved entity. ``entity_key`` is the stable, cross-run identifier."""

    __tablename__ = "research_entities"
    __table_args__ = (
        UniqueConstraint("research_job_id", "entity_key", name="uq_entity_job_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    research_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True
    )
    entity_key: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    canonical_name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    scope_domain: Mapped[str | None] = mapped_column(String(253), index=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    aliases: Mapped[list[str]] = mapped_column(JSONType, default=list)
    source_urls: Mapped[list[str]] = mapped_column(JSONType, default=list)
    match_reasons: Mapped[list[str]] = mapped_column(JSONType, default=list)
    confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchClaimRecord(Base):
    """An evidence-backed claim."""

    __tablename__ = "research_claims"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    research_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_entities.id", ondelete="SET NULL"), index=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_sources.id", ondelete="SET NULL"), index=True
    )
    subject_entity: Mapped[str] = mapped_column(Text)
    subject_type: Mapped[str | None] = mapped_column(String(32))
    scope_domain: Mapped[str | None] = mapped_column(String(253))
    predicate: Mapped[str] = mapped_column(String(64), index=True)
    object_value: Mapped[str] = mapped_column(Text)
    claim_text: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    source_title: Mapped[str | None] = mapped_column(Text)
    source_domain: Mapped[str] = mapped_column(String(253))
    source_content_hash: Mapped[str | None] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(16))
    evidence_excerpt: Mapped[str] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(16))
    verification_status: Mapped[str] = mapped_column(String(24), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    notes: Mapped[list[str]] = mapped_column(JSONType, default=list)
    publication_date: Mapped[str | None] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchReportRecord(Base):
    """A generated report in JSON and Markdown."""

    __tablename__ = "research_reports"
    __table_args__ = (
        UniqueConstraint("research_job_id", "report_type", name="uq_report_job_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    research_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="CASCADE"), index=True
    )
    report_type: Mapped[str] = mapped_column(String(32))
    content: Mapped[dict[str, Any]] = mapped_column(JSONType)
    markdown: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
