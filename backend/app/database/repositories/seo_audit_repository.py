"""PostgreSQL persistence for technical SEO audits (Phase 2).

Each public method commits its own unit of work. ``save_results`` writes pages,
findings and recommendations and the final audit status in one transaction,
so an audit is never left half-written.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    SeoAuditFindingRecord,
    SeoAuditPageRecord,
    SeoAuditRecommendationRecord,
    SeoAuditRecord,
)
from app.database.repositories.research_repository import _translate_errors
from app.schemas.research import utc_now
from app.seo.technical.models import Finding, PageResult, Recommendation


class SeoAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, record: SeoAuditRecord) -> SeoAuditRecord:
        async with _translate_errors(self._session):
            self._session.add(record)
            await self._session.commit()
        return record

    async def get(self, audit_id: UUID) -> SeoAuditRecord | None:
        async with _translate_errors(self._session):
            return await self._session.get(SeoAuditRecord, audit_id)

    async def refresh(self, record: SeoAuditRecord) -> None:
        """Reload a record changed by bulk UPDATEs in this session."""
        async with _translate_errors(self._session):
            await self._session.refresh(record)

    async def list_audits(
        self, *, host: str | None, limit: int, offset: int
    ) -> tuple[list[SeoAuditRecord], int]:
        query = select(SeoAuditRecord)
        count = select(func.count()).select_from(SeoAuditRecord)
        if host:
            query = query.where(SeoAuditRecord.host == host)
            count = count.where(SeoAuditRecord.host == host)
        async with _translate_errors(self._session):
            rows = (
                (
                    await self._session.execute(
                        query.order_by(SeoAuditRecord.created_at.desc())
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            total = (await self._session.execute(count)).scalar_one()
        return list(rows), int(total)

    async def running_for_host(self, host: str) -> SeoAuditRecord | None:
        async with _translate_errors(self._session):
            return (
                await self._session.execute(
                    select(SeoAuditRecord)
                    .where(
                        SeoAuditRecord.host == host, SeoAuditRecord.status == "running"
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def mark_running(self, audit_id: UUID, approval: dict[str, Any]) -> bool:
        """planned -> running (atomic); False if the audit was not ``planned``."""
        async with _translate_errors(self._session):
            result = await self._session.execute(
                update(SeoAuditRecord)
                .where(
                    SeoAuditRecord.id == audit_id, SeoAuditRecord.status == "planned"
                )
                .values(status="running", approval=approval, started_at=utc_now())
            )
            await self._session.commit()
        return (getattr(result, "rowcount", 0) or 0) == 1

    async def mark_failed(
        self, audit_id: UUID, message: str, usage: dict[str, Any]
    ) -> None:
        async with _translate_errors(self._session):
            await self._session.execute(
                update(SeoAuditRecord)
                .where(SeoAuditRecord.id == audit_id)
                .values(
                    status="failed",
                    error_summary=message,
                    usage=usage,
                    completed_at=utc_now(),
                )
            )
            await self._session.commit()

    async def save_results(
        self,
        audit_id: UUID,
        *,
        status: str,
        pages: list[PageResult],
        findings: list[Finding],
        recommendations: list[Recommendation],
        usage: dict[str, Any],
        coverage: dict[str, Any],
        site_files: dict[str, Any],
        summary: dict[str, Any],
    ) -> None:
        now = utc_now()
        async with _translate_errors(self._session):
            for page in pages:
                self._session.add(
                    SeoAuditPageRecord(
                        id=page.id,
                        audit_id=audit_id,
                        url=page.url,
                        final_url=page.final_url,
                        status_code=page.status_code,
                        status_source=page.status_source,
                        depth=page.depth,
                        discovered_via=page.discovered_via,
                        redirect_chain=[h.model_dump() for h in page.redirect_chain],
                        headers=page.headers,
                        fetch_source=page.fetch_source,
                        fetch_status=page.fetch_status,
                        fetch_detail=page.fetch_detail,
                        fetched_at=page.fetched_at,
                        html_sha256=page.html_sha256,
                        html_truncated=page.html_truncated,
                        firecrawl_status=page.firecrawl_status,
                        googlebot_allowed=page.googlebot_allowed,
                        signals=page.signals.model_dump(mode="json")
                        if page.signals
                        else None,
                        structured_data=page.structured_data.model_dump(mode="json")
                        if page.structured_data
                        else None,
                        indexability=page.indexability.model_dump(mode="json"),
                    )
                )
            await self._session.flush()
            for finding in findings:
                self._session.add(
                    SeoAuditFindingRecord(
                        id=finding.id,
                        audit_id=audit_id,
                        check_id=finding.check_id,
                        category=finding.category,
                        severity=finding.severity,
                        title=finding.title,
                        detail=finding.detail,
                        affected_urls=finding.affected_urls,
                        affected_count=finding.affected_count,
                        evidence=[e.model_dump(mode="json") for e in finding.evidence],
                        evidence_state=finding.evidence_state,
                        skill_refs=finding.skill_refs,
                        created_at=now,
                    )
                )
            await self._session.flush()
            for rec in recommendations:
                self._session.add(
                    SeoAuditRecommendationRecord(
                        id=rec.id,
                        audit_id=audit_id,
                        finding_id=rec.finding_id,
                        check_id=rec.check_id,
                        action=rec.action,
                        rationale=rec.rationale,
                        impact=rec.impact,
                        confidence=rec.confidence,
                        effort=rec.effort,
                        risk=rec.risk,
                        tier=rec.tier,
                        rank=rec.rank,
                        affected_count=rec.affected_count,
                        verification=rec.verification,
                        basis=rec.basis,
                        created_at=now,
                    )
                )
            await self._session.execute(
                update(SeoAuditRecord)
                .where(SeoAuditRecord.id == audit_id)
                .values(
                    status=status,
                    usage=usage,
                    coverage=coverage,
                    site_files=site_files,
                    summary=summary,
                    completed_at=now,
                )
            )
            await self._session.commit()

    async def pages(
        self,
        audit_id: UUID,
        *,
        limit: int,
        offset: int,
        fetch_status: str | None = None,
    ) -> tuple[list[SeoAuditPageRecord], int]:
        query = select(SeoAuditPageRecord).where(
            SeoAuditPageRecord.audit_id == audit_id
        )
        count = (
            select(func.count())
            .select_from(SeoAuditPageRecord)
            .where(SeoAuditPageRecord.audit_id == audit_id)
        )
        if fetch_status:
            query = query.where(SeoAuditPageRecord.fetch_status == fetch_status)
            count = count.where(SeoAuditPageRecord.fetch_status == fetch_status)
        async with _translate_errors(self._session):
            rows = (
                (
                    await self._session.execute(
                        query.order_by(SeoAuditPageRecord.depth, SeoAuditPageRecord.url)
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            total = (await self._session.execute(count)).scalar_one()
        return list(rows), int(total)

    async def page(self, audit_id: UUID, page_id: UUID) -> SeoAuditPageRecord | None:
        async with _translate_errors(self._session):
            record = await self._session.get(SeoAuditPageRecord, page_id)
        return record if record is not None and record.audit_id == audit_id else None

    async def findings(
        self,
        audit_id: UUID,
        *,
        severity: str | None = None,
        category: str | None = None,
    ) -> list[SeoAuditFindingRecord]:
        query = select(SeoAuditFindingRecord).where(
            SeoAuditFindingRecord.audit_id == audit_id
        )
        if severity:
            query = query.where(SeoAuditFindingRecord.severity == severity)
        if category:
            query = query.where(SeoAuditFindingRecord.category == category)
        async with _translate_errors(self._session):
            rows = (await self._session.execute(query)).scalars().all()
        order = {"high": 0, "medium": 1, "low": 2, "info": 3}
        return sorted(rows, key=lambda r: (order.get(r.severity, 9), r.check_id))

    async def recommendations(
        self, audit_id: UUID, *, tier: str | None = None
    ) -> list[SeoAuditRecommendationRecord]:
        query = select(SeoAuditRecommendationRecord).where(
            SeoAuditRecommendationRecord.audit_id == audit_id
        )
        if tier:
            query = query.where(SeoAuditRecommendationRecord.tier == tier)
        async with _translate_errors(self._session):
            rows = (
                (
                    await self._session.execute(
                        query.order_by(SeoAuditRecommendationRecord.rank)
                    )
                )
                .scalars()
                .all()
            )
        return list(rows)
