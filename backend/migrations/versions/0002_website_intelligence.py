"""Website intelligence tables (Module 3). Additive only; no data is modified.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _fk(table: str, column: str = "research_job_id", ondelete: str = "CASCADE"):
    return sa.Column(
        column,
        sa.Uuid(),
        sa.ForeignKey(f"{table}.id", ondelete=ondelete),
        nullable=ondelete != "CASCADE",
    )


def upgrade() -> None:
    op.add_column(
        "research_jobs",
        sa.Column("job_type", sa.String(32), nullable=False, server_default="research"),
    )

    op.create_table(
        "research_websites",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("research_jobs"),
        sa.Column("website_name", sa.String(200), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("homepage_url", sa.Text(), nullable=False),
        sa.Column("official_status", sa.String(32), nullable=False),
        sa.Column("website_type", sa.String(32), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("discovery_metadata", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("research_job_id", "domain", name="uq_website_job_domain"),
    )
    op.create_index(
        "ix_research_websites_research_job_id", "research_websites", ["research_job_id"]
    )
    op.create_index("ix_research_websites_domain", "research_websites", ["domain"])

    op.create_table(
        "mapped_pages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("research_websites", "website_id"),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("page_type", sa.String(32), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("crawl_eligible", sa.Boolean(), nullable=False),
        sa.Column(
            "crawl_status", sa.String(32), nullable=False, server_default="pending"
        ),
        sa.Column("exclusion_reason", sa.String(100), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discovery_method", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("website_id", "normalized_url", name="uq_page_website_url"),
    )
    op.create_index("ix_mapped_pages_website_id", "mapped_pages", ["website_id"])
    op.create_index("ix_mapped_pages_page_type", "mapped_pages", ["page_type"])

    op.create_table(
        "processed_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("research_jobs"),
        _fk("research_sources", "source_id"),
        sa.Column("page_type", sa.String(32), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("original_hash", sa.String(64), nullable=True),
        sa.Column("normalized_content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("sections", JSONB, nullable=False),
        sa.Column("document_metadata", JSONB, nullable=False),
        sa.Column("published_at", sa.String(64), nullable=True),
        sa.Column("modified_at", sa.String(64), nullable=True),
        sa.Column("processing_status", sa.String(32), nullable=False),
        sa.Column("duplicate_of", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_processed_documents_research_job_id",
        "processed_documents",
        ["research_job_id"],
    )
    op.create_index(
        "ix_processed_documents_source_id", "processed_documents", ["source_id"]
    )
    op.create_index(
        "ix_processed_documents_content_hash", "processed_documents", ["content_hash"]
    )

    op.create_table(
        "research_entities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("research_jobs"),
        sa.Column("entity_key", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scope_domain", sa.String(253), nullable=True),
        sa.Column("attributes", JSONB, nullable=False),
        sa.Column("aliases", JSONB, nullable=False),
        sa.Column("source_urls", JSONB, nullable=False),
        sa.Column("match_reasons", JSONB, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("research_job_id", "entity_key", name="uq_entity_job_key"),
    )
    for column in (
        "research_job_id",
        "entity_key",
        "entity_type",
        "normalized_name",
        "scope_domain",
    ):
        op.create_index(f"ix_research_entities_{column}", "research_entities", [column])

    op.create_table(
        "research_claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("research_jobs"),
        _fk("research_entities", "entity_id", "SET NULL"),
        _fk("research_sources", "source_id", "SET NULL"),
        sa.Column("subject_entity", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.String(32), nullable=True),
        sa.Column("scope_domain", sa.String(253), nullable=True),
        sa.Column("predicate", sa.String(64), nullable=False),
        sa.Column("object_value", sa.Text(), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_title", sa.Text(), nullable=True),
        sa.Column("source_domain", sa.String(253), nullable=False),
        sa.Column("source_content_hash", sa.String(64), nullable=True),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("evidence_excerpt", sa.Text(), nullable=False),
        sa.Column("extraction_method", sa.String(16), nullable=False),
        sa.Column("verification_status", sa.String(24), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("notes", JSONB, nullable=False),
        sa.Column("publication_date", sa.String(64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in (
        "research_job_id",
        "entity_id",
        "source_id",
        "predicate",
        "verification_status",
    ):
        op.create_index(f"ix_research_claims_{column}", "research_claims", [column])

    op.create_table(
        "research_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("research_jobs"),
        sa.Column("report_type", sa.String(32), nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "research_job_id", "report_type", name="uq_report_job_type"
        ),
    )
    op.create_index(
        "ix_research_reports_research_job_id", "research_reports", ["research_job_id"]
    )


def downgrade() -> None:
    for table in (
        "research_reports",
        "research_claims",
        "research_entities",
        "processed_documents",
        "mapped_pages",
        "research_websites",
    ):
        op.drop_table(table)
    op.drop_column("research_jobs", "job_type")
