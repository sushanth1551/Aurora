"""Search Console monitoring tables (Phase 6). Additive only.

Creates ``gsc_connection``, ``gsc_sync_runs``, ``gsc_daily``, ``gsc_performance``,
``gsc_mappings``, ``gsc_analysis_runs``, ``gsc_insights``, ``gsc_refresh_tasks`` and
``gsc_schedules``. No existing table is altered. The article references in
``gsc_mappings`` and ``gsc_refresh_tasks`` are plain UUID columns without foreign
keys, so this migration does not depend on how articles are stored.

Numbering: 0007 is reserved by Phase 4 (content) and 0008 by Phase 5 (publishing).
Until those merge, this revision is chained to 0006; when they land, set
``down_revision`` to the latest merged revision (a one-line change).

Revision ID: 0009
Revises: 0006
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
TABLES = (
    "gsc_schedules",
    "gsc_refresh_tasks",
    "gsc_insights",
    "gsc_analysis_runs",
    "gsc_mappings",
    "gsc_performance",
    "gsc_daily",
    "gsc_sync_runs",
    "gsc_connection",
)


def _ts(name: str, nullable: bool = False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _index(table: str, column: str) -> None:
    op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    op.create_table(
        "gsc_connection",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("refresh_token_sealed", sa.Text(), nullable=True),
        sa.Column("vault", sa.String(16), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("selected_property", sa.String(512), nullable=True),
        _ts("connected_at", nullable=True),
        _ts("last_verified_at", nullable=True),
        sa.Column("last_error_category", sa.String(64), nullable=True),
        _ts("updated_at"),
    )

    op.create_table(
        "gsc_sync_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("property", sa.String(512), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=True),
        sa.Column("limits", JSONB, nullable=False),
        sa.Column("estimate", JSONB, nullable=False),
        sa.Column("approval", JSONB, nullable=True),
        sa.Column("usage", JSONB, nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("started_at", nullable=True),
        _ts("completed_at", nullable=True),
    )
    for column in ("property", "status", "created_at"):
        _index("gsc_sync_runs", column)

    op.create_table(
        "gsc_daily",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("property", sa.String(512), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("clicks", sa.Float(), nullable=False),
        sa.Column("impressions", sa.Float(), nullable=False),
        sa.Column("ctr", sa.Float(), nullable=False),
        sa.Column("position", sa.Float(), nullable=False),
        sa.Column(
            "sync_run_id",
            sa.Uuid(),
            sa.ForeignKey("gsc_sync_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _ts("fetched_at"),
        sa.UniqueConstraint("property", "date", name="uq_gsc_daily"),
    )
    for column in ("property", "date"):
        _index("gsc_daily", column)

    op.create_table(
        "gsc_performance",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("row_key", sa.String(64), nullable=False, unique=True),
        sa.Column("property", sa.String(512), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("page", sa.Text(), nullable=False),
        sa.Column("search_type", sa.String(16), nullable=False),
        sa.Column("clicks", sa.Float(), nullable=False),
        sa.Column("impressions", sa.Float(), nullable=False),
        sa.Column("ctr", sa.Float(), nullable=False),
        sa.Column("position", sa.Float(), nullable=False),
        sa.Column(
            "sync_run_id",
            sa.Uuid(),
            sa.ForeignKey("gsc_sync_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _ts("fetched_at"),
    )
    op.create_index(
        "ix_gsc_performance_property_date", "gsc_performance", ["property", "date"]
    )

    op.create_table(
        "gsc_mappings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("property", sa.String(512), nullable=False),
        sa.Column("page_url", sa.Text(), nullable=False),
        sa.Column("page_key", sa.String(64), nullable=False),
        sa.Column("keyword", sa.Text(), nullable=True),
        sa.Column("keyword_key", sa.String(64), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=True),
        sa.Column("article_version_id", sa.Uuid(), nullable=True),
        sa.Column("publication_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        _ts("published_at", nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint(
            "property", "page_key", "keyword_key", name="uq_gsc_mapping"
        ),
    )
    for column in ("property", "article_id"):
        _index("gsc_mappings", column)

    op.create_table(
        "gsc_analysis_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("property", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("params", JSONB, nullable=False),
        sa.Column("window", JSONB, nullable=False),
        sa.Column("summary", JSONB, nullable=False),
        sa.Column("notes", JSONB, nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        _ts("created_at"),
    )
    for column in ("property", "created_at"):
        _index("gsc_analysis_runs", column)

    op.create_table(
        "gsc_insights",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "analysis_id",
            sa.Uuid(),
            sa.ForeignKey("gsc_analysis_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("impact", sa.String(8), nullable=False),
        sa.Column("confidence", sa.String(8), nullable=False),
        sa.Column("effort", sa.String(8), nullable=False),
        sa.Column("risk", sa.String(8), nullable=False),
        sa.Column("tier", sa.String(24), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("verification", sa.Text(), nullable=False),
        sa.Column("basis", sa.String(16), nullable=False),
        sa.Column("refresh_candidate", sa.Boolean(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
    )
    for column in ("analysis_id", "kind", "tier", "status"):
        _index("gsc_insights", column)

    op.create_table(
        "gsc_refresh_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "insight_id",
            sa.Uuid(),
            sa.ForeignKey("gsc_insights.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("property", sa.String(512), nullable=False),
        sa.Column("page_url", sa.Text(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=True),
        sa.Column("article_version_id", sa.Uuid(), nullable=True),
        sa.Column("brief", JSONB, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("requires_new_approval", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("insight_id", name="uq_gsc_refresh_task_insight"),
    )
    for column in ("insight_id", "property", "status"):
        _index("gsc_refresh_tasks", column)

    op.create_table(
        "gsc_schedules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("property", sa.String(512), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("interval_hours", sa.Integer(), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=False),
        sa.Column("budget_requests", sa.Integer(), nullable=False),
        sa.Column("approved_by", sa.String(120), nullable=True),
        _ts("approved_at", nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        _ts("next_run_at", nullable=True),
        _ts("last_run_at", nullable=True),
        sa.Column("last_status", sa.String(32), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        _ts("created_at"),
    )
    for column in ("property", "enabled", "next_run_at"):
        _index("gsc_schedules", column)


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)
