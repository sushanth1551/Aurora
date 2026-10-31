"""Intelligence synthesis tables (Module 4). Additive only; no data is modified.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
TABLES = (
    "report_quality_evaluations",
    "llm_usage_records",
    "intelligence_reports",
    "research_contradictions",
    "intelligence_findings",
    "research_questions",
    "research_contexts",
    "intelligence_analyses",
)


def _fk(table: str, column: str = "analysis_id"):
    return sa.Column(
        column,
        sa.Uuid(),
        sa.ForeignKey(f"{table}.id", ondelete="CASCADE"),
        nullable=False,
    )


def _ts(name: str, nullable: bool = False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _index(table: str, column: str) -> None:
    op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    op.create_table(
        "intelligence_analyses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("research_jobs", "research_job_id"),
        sa.Column("research_objective", sa.Text(), nullable=False),
        sa.Column("analysis_type", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("iteration_count", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        _ts("started_at"),
        _ts("completed_at", nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("configuration", JSONB, nullable=False),
        sa.Column("progress", JSONB, nullable=False),
    )
    _index("intelligence_analyses", "research_job_id")
    _index("intelligence_analyses", "status")

    op.create_table(
        "research_contexts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("intelligence_analyses"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_references", JSONB, nullable=False),
        sa.Column("claim_references", JSONB, nullable=False),
        sa.Column("statistics", JSONB, nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint(
            "analysis_id", "version", name="uq_context_analysis_version"
        ),
    )
    _index("research_contexts", "analysis_id")

    op.create_table(
        "research_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("intelligence_analyses"),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("related_entity", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("evidence_references", JSONB, nullable=False),
        _ts("created_at"),
    )
    _index("research_questions", "analysis_id")

    op.create_table(
        "intelligence_findings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("intelligence_analyses"),
        sa.Column("finding_type", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("claim_references", JSONB, nullable=False),
        sa.Column("evidence_references", JSONB, nullable=False),
        sa.Column("confidence", JSONB, nullable=False),
        _ts("created_at"),
    )
    _index("intelligence_findings", "analysis_id")
    _index("intelligence_findings", "finding_type")

    op.create_table(
        "research_contradictions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("intelligence_analyses"),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("conflicting_claims", JSONB, nullable=False),
        sa.Column("sources", JSONB, nullable=False),
        sa.Column("resolution_status", sa.String(24), nullable=False),
        _ts("created_at"),
    )
    _index("research_contradictions", "analysis_id")

    op.create_table(
        "intelligence_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("intelligence_analyses"),
        sa.Column("report_type", sa.String(32), nullable=False),
        sa.Column("content_json", JSONB, nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("quality_status", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint(
            "analysis_id", "version", name="uq_ireport_analysis_version"
        ),
    )
    _index("intelligence_reports", "analysis_id")

    op.create_table(
        "llm_usage_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("intelligence_analyses"),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(48), nullable=False),
        sa.Column("prompt_version", sa.String(96), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("cost_available", sa.Boolean(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_category", sa.String(64), nullable=True),
        sa.Column("fallback_from", sa.String(32), nullable=True),
        sa.Column("fallback_reason", sa.String(64), nullable=True),
        _ts("requested_at"),
    )
    _index("llm_usage_records", "analysis_id")

    op.create_table(
        "report_quality_evaluations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk("intelligence_analyses"),
        _fk("intelligence_reports", "report_id"),
        sa.Column("metrics", JSONB, nullable=False),
        sa.Column("evaluation_status", sa.String(24), nullable=False),
        sa.Column("issues", JSONB, nullable=False),
        _ts("evaluated_at"),
    )
    _index("report_quality_evaluations", "analysis_id")
    _index("report_quality_evaluations", "report_id")


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)
