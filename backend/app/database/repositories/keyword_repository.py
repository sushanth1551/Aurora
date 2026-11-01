"""PostgreSQL persistence for keyword research (Phase 3).

Each public method commits its own unit of work. ``save_results`` writes the
candidates, opportunities and the final run state in one transaction.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    SeoKeywordCandidateRecord,
    SeoKeywordOpportunityRecord,
    SeoKeywordRunRecord,
    SeoSerpSnapshotRecord,
)
from app.database.repositories.research_repository import _translate_errors
from app.schemas.research import utc_now
from app.seo.keywords.models import Candidate, Opportunity, as_dict
from app.tools.serpapi_features import SerpSnapshotData


class KeywordRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- runs -----------------------------------------------------------------------

    async def create_run(self, record: SeoKeywordRunRecord) -> SeoKeywordRunRecord:
        async with _translate_errors(self._session):
            self._session.add(record)
            await self._session.commit()
        return record

    async def get_run(self, run_id: UUID) -> SeoKeywordRunRecord | None:
        async with _translate_errors(self._session):
            return await self._session.get(SeoKeywordRunRecord, run_id)

    async def refresh(self, record: SeoKeywordRunRecord) -> None:
        async with _translate_errors(self._session):
            await self._session.refresh(record)

    async def list_runs(
        self, *, host: str | None, limit: int, offset: int
    ) -> tuple[list[SeoKeywordRunRecord], int]:
        query = select(SeoKeywordRunRecord)
        count = select(func.count()).select_from(SeoKeywordRunRecord)
        if host:
            query = query.where(SeoKeywordRunRecord.host == host)
            count = count.where(SeoKeywordRunRecord.host == host)
        async with _translate_errors(self._session):
            rows = (
                (
                    await self._session.execute(
                        query.order_by(SeoKeywordRunRecord.created_at.desc())
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            total = (await self._session.execute(count)).scalar_one()
        return list(rows), int(total)

    async def running_for_host(self, host: str) -> SeoKeywordRunRecord | None:
        async with _translate_errors(self._session):
            return (
                await self._session.execute(
                    select(SeoKeywordRunRecord)
                    .where(
                        SeoKeywordRunRecord.host == host,
                        SeoKeywordRunRecord.status == "running",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def mark_running(self, run_id: UUID, approval: dict[str, Any]) -> bool:
        """planned -> running (atomic); False if the run was not ``planned``."""
        async with _translate_errors(self._session):
            result = await self._session.execute(
                update(SeoKeywordRunRecord)
                .where(
                    SeoKeywordRunRecord.id == run_id,
                    SeoKeywordRunRecord.status == "planned",
                )
                .values(status="running", approval=approval, started_at=utc_now())
            )
            await self._session.commit()
        return (getattr(result, "rowcount", 0) or 0) == 1

    async def mark_failed(
        self, run_id: UUID, message: str, usage: dict[str, Any]
    ) -> None:
        async with _translate_errors(self._session):
            await self._session.execute(
                update(SeoKeywordRunRecord)
                .where(SeoKeywordRunRecord.id == run_id)
                .values(
                    status="failed",
                    error_summary=message,
                    usage=usage,
                    completed_at=utc_now(),
                )
            )
            await self._session.commit()

    # -- SERP snapshots -------------------------------------------------------------

    async def save_snapshot(self, data: SerpSnapshotData, normalized: str) -> UUID:
        snapshot_id = uuid4()
        async with _translate_errors(self._session):
            self._session.add(
                SeoSerpSnapshotRecord(
                    id=snapshot_id,
                    query=data.query,
                    normalized_query=normalized,
                    country=data.country,
                    language=data.language,
                    device=data.device,
                    location=data.location,
                    provider="serpapi",
                    provider_search_id=data.provider_search_id,
                    observed_at=utc_now(),
                    organic=[r.model_dump(mode="json") for r in data.organic],
                    features=data.features.model_dump(mode="json"),
                )
            )
            await self._session.commit()
        return snapshot_id

    async def fresh_snapshot(
        self,
        normalized: str,
        *,
        country: str | None,
        language: str | None,
        device: str | None,
        location: str | None,
        ttl_hours: int,
    ) -> SeoSerpSnapshotRecord | None:
        """Newest snapshot of the same query and parameters within the TTL."""
        if ttl_hours <= 0:
            return None
        cutoff = utc_now() - timedelta(hours=ttl_hours)
        query = select(SeoSerpSnapshotRecord).where(
            SeoSerpSnapshotRecord.normalized_query == normalized,
            SeoSerpSnapshotRecord.observed_at >= cutoff,
        )
        for column, value in (
            (SeoSerpSnapshotRecord.country, country),
            (SeoSerpSnapshotRecord.language, language),
            (SeoSerpSnapshotRecord.device, device),
            (SeoSerpSnapshotRecord.location, location),
        ):
            query = query.where(column.is_(None) if value is None else column == value)
        async with _translate_errors(self._session):
            return (
                await self._session.execute(
                    query.order_by(SeoSerpSnapshotRecord.observed_at.desc()).limit(1)
                )
            ).scalar_one_or_none()

    async def get_snapshot(self, snapshot_id: UUID) -> SeoSerpSnapshotRecord | None:
        async with _translate_errors(self._session):
            return await self._session.get(SeoSerpSnapshotRecord, snapshot_id)

    # -- results ------------------------------------------------------------------

    async def save_results(
        self,
        run_id: UUID,
        *,
        status: str,
        candidates: list[Candidate],
        snapshot_ids: dict[str, UUID],
        opportunities: list[Opportunity],
        usage: dict[str, Any],
        summary: dict[str, Any],
        competitors: list[dict[str, Any]],
        gaps: list[dict[str, Any]],
        gap_status: str,
    ) -> None:
        async with _translate_errors(self._session):
            candidate_ids: dict[str, UUID] = {}
            for cand in candidates:
                cid = uuid4()
                candidate_ids[cand.normalized] = cid
                self._session.add(
                    SeoKeywordCandidateRecord(
                        id=cid,
                        run_id=run_id,
                        keyword=cand.keyword,
                        normalized=cand.normalized[:300],
                        source=cand.source,
                        source_detail=cand.source_detail,
                        intent=cand.intent,
                        intent_basis=cand.intent_basis,
                        cluster=cand.cluster,
                        searched=cand.searched,
                        snapshot_id=snapshot_ids.get(cand.normalized),
                        metrics={k: as_dict(v) for k, v in cand.metrics.items()},
                    )
                )
            await self._session.flush()
            for opp in opportunities:
                self._session.add(
                    SeoKeywordOpportunityRecord(
                        id=uuid4(),
                        run_id=run_id,
                        candidate_id=candidate_ids[opp.normalized],
                        keyword=opp.keyword,
                        normalized=opp.normalized[:300],
                        rank=opp.rank,
                        action=opp.action,
                        intent=opp.intent,
                        intent_basis=opp.intent_basis,
                        cluster=opp.cluster,
                        impact=opp.impact,
                        confidence=opp.confidence,
                        effort=opp.effort,
                        risk=opp.risk,
                        rationale=opp.rationale,
                        serp=as_dict(opp.serp),
                        gap_terms=opp.gap_terms,
                        questions=opp.questions,
                        metrics={k: as_dict(v) for k, v in opp.metrics.items()},
                        basis=opp.basis,
                        selected=False,
                    )
                )
            await self._session.execute(
                update(SeoKeywordRunRecord)
                .where(SeoKeywordRunRecord.id == run_id)
                .values(
                    status=status,
                    usage=usage,
                    summary=summary,
                    competitors=competitors,
                    gaps=gaps,
                    gap_status=gap_status,
                    completed_at=utc_now(),
                )
            )
            await self._session.commit()

    async def candidates(
        self,
        run_id: UUID,
        *,
        intent: str | None = None,
        cluster: str | None = None,
        searched: bool | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[SeoKeywordCandidateRecord], int]:
        where = [SeoKeywordCandidateRecord.run_id == run_id]
        if intent:
            where.append(SeoKeywordCandidateRecord.intent == intent)
        if cluster:
            where.append(SeoKeywordCandidateRecord.cluster == cluster)
        if searched is not None:
            where.append(SeoKeywordCandidateRecord.searched == searched)
        async with _translate_errors(self._session):
            rows = (
                (
                    await self._session.execute(
                        select(SeoKeywordCandidateRecord)
                        .where(*where)
                        .order_by(SeoKeywordCandidateRecord.normalized)
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            total = (
                await self._session.execute(
                    select(func.count())
                    .select_from(SeoKeywordCandidateRecord)
                    .where(*where)
                )
            ).scalar_one()
        return list(rows), int(total)

    async def opportunities(
        self,
        run_id: UUID,
        *,
        action: str | None = None,
        selected: bool | None = None,
    ) -> list[SeoKeywordOpportunityRecord]:
        query = select(SeoKeywordOpportunityRecord).where(
            SeoKeywordOpportunityRecord.run_id == run_id
        )
        if action:
            query = query.where(SeoKeywordOpportunityRecord.action == action)
        if selected is not None:
            query = query.where(SeoKeywordOpportunityRecord.selected == selected)
        async with _translate_errors(self._session):
            rows = (
                (
                    await self._session.execute(
                        query.order_by(SeoKeywordOpportunityRecord.rank)
                    )
                )
                .scalars()
                .all()
            )
        return list(rows)

    async def opportunity(
        self, opportunity_id: UUID
    ) -> SeoKeywordOpportunityRecord | None:
        async with _translate_errors(self._session):
            return await self._session.get(SeoKeywordOpportunityRecord, opportunity_id)

    async def set_selected(
        self, opportunity_id: UUID, selected: bool
    ) -> SeoKeywordOpportunityRecord | None:
        async with _translate_errors(self._session):
            record = await self._session.get(
                SeoKeywordOpportunityRecord, opportunity_id
            )
            if record is None:
                return None
            record.selected = selected
            record.selected_at = utc_now() if selected else None
            await self._session.commit()
        return record

    async def selected_opportunities(
        self, host: str | None, limit: int = 100
    ) -> list[tuple[SeoKeywordOpportunityRecord, str, datetime]]:
        """Selected opportunities (newest run first) with their run's host and time."""
        query = (
            select(
                SeoKeywordOpportunityRecord,
                SeoKeywordRunRecord.host,
                SeoKeywordRunRecord.created_at,
            )
            .join(
                SeoKeywordRunRecord,
                SeoKeywordRunRecord.id == SeoKeywordOpportunityRecord.run_id,
            )
            .where(SeoKeywordOpportunityRecord.selected.is_(True))
        )
        if host:
            query = query.where(SeoKeywordRunRecord.host == host)
        async with _translate_errors(self._session):
            rows = (
                await self._session.execute(
                    query.order_by(SeoKeywordRunRecord.created_at.desc()).limit(limit)
                )
            ).all()
        return [(r[0], r[1], r[2]) for r in rows]
