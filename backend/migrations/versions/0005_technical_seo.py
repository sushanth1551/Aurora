"""Technical SEO audit tables (Phase 2). Additive only.

Creates ``seo_audits`` and its children ``seo_audit_pages``,
``seo_audit_findings`` and ``seo_audit_recommendations`` (ON DELETE CASCADE from
the audit). No existing table is altered.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
TABLES = (
    "seo_audit_recommendations",
    "seo_audit_findings",
    "seo_audit_pages",
    "seo_audits",
)


def _ts(name: str, nullable: bool = False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _index(table: str, column: str) -> None:
    op.create_index(f"ix_{table}_{column}", table, [column])


def _audit_fk() -> sa.Column:
    return sa.Column(
        "audit_id",
        sa.Uuid(),
        sa.ForeignKey("seo_audits.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "seo_audits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("site_url", sa.Text(), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("limits", JSONB, nullable=False),
        sa.Column("estimate", JSONB, nullable=False),
        sa.Column("approval", JSONB, nullable=True),
        sa.Column("usage", JSONB, nullable=False),
        sa.Column("coverage", JSONB, nullable=False),
        sa.Column("site_files", JSONB, nullable=False),
        sa.Column("summary", JSONB, nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("started_at", nullable=True),
        _ts("completed_at", nullable=True),
    )
    for column in ("host", "status", "created_at"):
        _index("seo_audits", column)

    op.create_table(
        "seo_audit_pages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _audit_fk(),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("status_source", sa.String(32), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("discovered_via", JSONB, nullable=False),
        sa.Column("redirect_chain", JSONB, nullable=False),
        sa.Column("headers", JSONB, nullable=False),
        sa.Column("fetch_source", sa.String(32), nullable=False),
        sa.Column("fetch_status", sa.String(32), nullable=False),
        sa.Column("fetch_detail", sa.Text(), nullable=True),
        _ts("fetched_at", nullable=True),
        sa.Column("html_sha256", sa.String(64), nullable=True),
        sa.Column("html_truncated", sa.Boolean(), nullable=False),
        sa.Column("firecrawl_status", sa.Integer(), nullable=True),
        sa.Column("googlebot_allowed", sa.Boolean(), nullable=True),
        sa.Column("signals", JSONB, nullable=True),
        sa.Column("structured_data", JSONB, nullable=True),
        sa.Column("indexability", JSONB, nullable=False),
        sa.UniqueConstraint("audit_id", "url", name="uq_seo_audit_page_url"),
    )
    _index("seo_audit_pages", "audit_id")
    _index("seo_audit_pages", "fetch_status")

    op.create_table(
        "seo_audit_findings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _audit_fk(),
        sa.Column("check_id", sa.String(64), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("affected_urls", JSONB, nullable=False),
        sa.Column("affected_count", sa.Integer(), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("evidence_state", sa.String(24), nullable=False),
        sa.Column("skill_refs", JSONB, nullable=False),
        _ts("created_at"),
    )
    for column in ("audit_id", "check_id", "category", "severity"):
        _index("seo_audit_findings", column)

    op.create_table(
        "seo_audit_recommendations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _audit_fk(),
        sa.Column(
            "finding_id",
            sa.Uuid(),
            sa.ForeignKey("seo_audit_findings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("check_id", sa.String(64), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("impact", sa.String(16), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("effort", sa.String(16), nullable=False),
        sa.Column("risk", sa.String(16), nullable=False),
        sa.Column("tier", sa.String(24), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("affected_count", sa.Integer(), nullable=False),
        sa.Column("verification", sa.Text(), nullable=False),
        sa.Column("basis", sa.String(24), nullable=False),
        _ts("created_at"),
    )
    for column in ("audit_id", "finding_id", "tier"):
        _index("seo_audit_recommendations", column)


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)
