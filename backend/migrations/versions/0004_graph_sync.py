"""Knowledge graph synchronization tables (Module 5). Additive only.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
TABLES = ("graph_entity_resolutions", "graph_sync_runs", "graph_sync_outbox")


def _ts(name: str, nullable: bool = False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _index(table: str, column: str) -> None:
    op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    op.create_table(
        "graph_sync_outbox",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("payload_reference", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        _ts("next_retry_at", nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _ts("processed_at", nullable=True),
        sa.UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "operation",
            name="uq_graph_outbox_aggregate_op",
        ),
    )
    _index("graph_sync_outbox", "aggregate_id")
    _index("graph_sync_outbox", "status")
    _index("graph_sync_outbox", "next_retry_at")

    op.create_table(
        "graph_sync_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_job_id",
            sa.Uuid(),
            sa.ForeignKey("research_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("counts", JSONB, nullable=False),
        sa.Column("rejected", JSONB, nullable=False),
        sa.Column("review", JSONB, nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        _ts("started_at"),
        _ts("completed_at", nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
    )
    _index("graph_sync_runs", "research_job_id")
    _index("graph_sync_runs", "status")

    op.create_table(
        "graph_entity_resolutions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_job_id",
            sa.Uuid(),
            sa.ForeignKey("research_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_key", sa.String(36), nullable=False),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("graph_node_id", sa.String(36), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reasons", JSONB, nullable=False),
        sa.Column("candidate_node_id", sa.String(36), nullable=True),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("match_key", sa.String(300), nullable=False),
        sa.Column("scope_domain", sa.String(253), nullable=True),
        sa.Column("aliases", JSONB, nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint(
            "research_job_id", "entity_key", name="uq_graph_resolution_job_entity"
        ),
    )
    for column in (
        "research_job_id",
        "entity_key",
        "graph_node_id",
        "decision",
        "match_key",
        "scope_domain",
    ):
        _index("graph_entity_resolutions", column)


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)
