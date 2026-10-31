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
