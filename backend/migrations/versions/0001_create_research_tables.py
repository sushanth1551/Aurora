"""Create research job, search result and source tables.

Revision ID: 0001
Revises:
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _job_fk() -> sa.Column:
    return sa.Column(
        "research_job_id",
        sa.Uuid(),
        sa.ForeignKey("research_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "research_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.String(128), nullable=True, unique=True),
        sa.Column("query", sa.String(500), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("max_results", sa.Integer(), nullable=False),
        sa.Column("scrape_pages", sa.Boolean(), nullable=False),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("allowed_domains", JSONB, nullable=True),
        sa.Column(
            "total_search_results", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "eligible_source_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "successful_source_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "failed_source_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("error_category", sa.String(64), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("errors", JSONB, nullable=False, server_default="[]"),
        sa.Column("stage_summaries", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_research_jobs_status", "research_jobs", ["status"])
    op.create_index("ix_research_jobs_created_at", "research_jobs", ["created_at"])

    op.create_table(
        "search_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _job_fk(),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("displayed_link", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(253), nullable=True),
        sa.Column("provider_metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_search_results_research_job_id", "search_results", ["research_job_id"]
    )
    op.create_index("ix_search_results_domain", "search_results", ["domain"])

    op.create_table(
        "research_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _job_fk(),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=True),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(253), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("extracted_markdown", sa.Text(), nullable=True),
        sa.Column("extraction_status", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("provider_metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "research_job_id", "normalized_url", name="uq_source_job_normalized_url"
        ),
    )
    for column in (
        "research_job_id",
        "normalized_url",
        "domain",
        "extraction_status",
        "content_hash",
    ):
        op.create_index(f"ix_research_sources_{column}", "research_sources", [column])


def downgrade() -> None:
    op.drop_table("research_sources")
    op.drop_table("search_results")
    op.drop_table("research_jobs")
