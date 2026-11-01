"""Keyword research tables (Phase 3). Additive only.

Creates ``seo_keyword_runs``, ``seo_serp_snapshots``, ``seo_keyword_candidates``
and ``seo_keyword_opportunities``. Candidates and opportunities cascade from the
run; a candidate's snapshot reference is ``ON DELETE SET NULL``. No existing
table is altered.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
TABLES = (
    "seo_keyword_opportunities",
    "seo_keyword_candidates",
    "seo_serp_snapshots",
    "seo_keyword_runs",
)


def _ts(name: str, nullable: bool = False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _index(table: str, column: str) -> None:
    op.create_index(f"ix_{table}_{column}", table, [column])


def _run_fk() -> sa.Column:
    return sa.Column(
        "run_id",
        sa.Uuid(),
        sa.ForeignKey("seo_keyword_runs.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "seo_keyword_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("site_url", sa.Text(), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("params", JSONB, nullable=False),
        sa.Column("limits", JSONB, nullable=False),
        sa.Column("estimate", JSONB, nullable=False),
        sa.Column("approval", JSONB, nullable=True),
        sa.Column("usage", JSONB, nullable=False),
        sa.Column("summary", JSONB, nullable=False),
        sa.Column("competitors", JSONB, nullable=False),
        sa.Column("gaps", JSONB, nullable=False),
        sa.Column("gap_status", sa.String(32), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("started_at", nullable=True),
        _ts("completed_at", nullable=True),
    )
    for column in ("host", "status", "created_at"):
        _index("seo_keyword_runs", column)

    op.create_table(
        "seo_serp_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("normalized_query", sa.String(300), nullable=False),
        sa.Column("country", sa.String(8), nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("device", sa.String(16), nullable=True),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_search_id", sa.String(100), nullable=True),
        _ts("observed_at"),
        sa.Column("organic", JSONB, nullable=False),
        sa.Column("features", JSONB, nullable=False),
    )
    for column in ("normalized_query", "observed_at"):
        _index("seo_serp_snapshots", column)

    op.create_table(
        "seo_keyword_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _run_fk(),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("normalized", sa.String(300), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_detail", sa.Text(), nullable=True),
        sa.Column("intent", sa.String(16), nullable=True),
        sa.Column("intent_basis", sa.String(100), nullable=True),
        sa.Column("cluster", sa.String(300), nullable=True),
        sa.Column("searched", sa.Boolean(), nullable=False),
        sa.Column(
            "snapshot_id",
            sa.Uuid(),
            sa.ForeignKey("seo_serp_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metrics", JSONB, nullable=False),
        sa.UniqueConstraint("run_id", "normalized", name="uq_seo_keyword_candidate"),
    )
    for column in ("run_id", "intent", "cluster", "snapshot_id"):
        _index("seo_keyword_candidates", column)

    op.create_table(
        "seo_keyword_opportunities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _run_fk(),
        sa.Column(
            "candidate_id",
            sa.Uuid(),
            sa.ForeignKey("seo_keyword_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("normalized", sa.String(300), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("intent", sa.String(16), nullable=True),
        sa.Column("intent_basis", sa.String(100), nullable=True),
        sa.Column("cluster", sa.String(300), nullable=True),
        sa.Column("impact", sa.String(16), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("effort", sa.String(16), nullable=False),
        sa.Column("risk", sa.String(16), nullable=False),
        sa.Column("rationale", JSONB, nullable=False),
        sa.Column("serp", JSONB, nullable=False),
        sa.Column("gap_terms", JSONB, nullable=False),
        sa.Column("questions", JSONB, nullable=False),
        sa.Column("metrics", JSONB, nullable=False),
        sa.Column("basis", sa.String(16), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        _ts("selected_at", nullable=True),
        sa.UniqueConstraint("run_id", "normalized", name="uq_seo_keyword_opportunity"),
    )
    for column in ("run_id", "candidate_id", "action", "selected"):
        _index("seo_keyword_opportunities", column)


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)
