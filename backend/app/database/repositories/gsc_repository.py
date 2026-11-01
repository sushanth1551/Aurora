"""PostgreSQL persistence for Search Console monitoring (Phase 6).

Each public method commits its own unit of work. Performance rows are stored
exactly as Google returned them (final data only) and are inserted idempotently:
a row whose natural key already exists is skipped, never overwritten, so a sync can
be repeated safely. Nothing here deletes performance history.
"""

import hashlib
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    GscAnalysisRunRecord,
    GscConnectionRecord,
    GscDailyRecord,
    GscInsightRecord,
    GscMappingRecord,
    GscPerformanceRecord,
    GscRefreshTaskRecord,
    GscScheduleRecord,
    GscSyncRunRecord,
)
from app.database.repositories.research_repository import _translate_errors
from app.monitoring.models import Agg, InsightDraft
from app.schemas.research import utc_now

CONNECTION_ID = "default"
KEY_CHUNK = 400  # stays below SQLite's bound-parameter limit
INSERT_CHUNK = 500
MAX_AGG_ROWS = 50_000
MAX_SEEN_QUERIES = 300_000


def sha(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def performance_key(
    property_id: str, day: date, query: str, page: str, search_type: str
) -> str:
    return sha(property_id, day.isoformat(), query, page, search_type)


def page_key(page_url: str) -> str:
    return sha(normalize_page(page_url))


def keyword_key(keyword: str | None) -> str:
    return sha(" ".join((keyword or "").lower().split()))


def normalize_page(url: str) -> str:
    """Compare pages without fragment, with a single trailing slash form."""
    url = url.strip().split("#", 1)[0]
    return url[:-1] if url.endswith("/") and url.count("/") > 3 else url


class GscRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- connection -----------------------------------------------------------------

    async def get_connection(self) -> GscConnectionRecord | None:
        async with _translate_errors(self._session):
            return await self._session.get(GscConnectionRecord, CONNECTION_ID)

    async def save_connection(
        self,
        *,
        state: str,
        sealed: str | None,
        vault: str | None,
        scope: str | None,
        keep_property: bool = True,
    ) -> GscConnectionRecord:
        now = utc_now()
        async with _translate_errors(self._session):
            record = await self._session.get(GscConnectionRecord, CONNECTION_ID)
            if record is None:
                record = GscConnectionRecord(
                    id=CONNECTION_ID, state=state, updated_at=now
                )
                self._session.add(record)
            record.state = state
            record.refresh_token_sealed = sealed
            record.vault = vault
            record.scope = scope
            record.connected_at = now if sealed else record.connected_at
            record.last_error_category = None
            if not keep_property:
                record.selected_property = None
            record.updated_at = now
            await self._session.commit()
        return record

    async def set_connection_state(
        self, state: str, *, error: str | None = None, verified: bool = False
    ) -> None:
        now = utc_now()
        async with _translate_errors(self._session):
            record = await self._session.get(GscConnectionRecord, CONNECTION_ID)
            if record is None:
                record = GscConnectionRecord(
                    id=CONNECTION_ID, state=state, updated_at=now
                )
                self._session.add(record)
            record.state = state
            record.last_error_category = error
            if verified:
                record.last_verified_at = now
            record.updated_at = now
            await self._session.commit()

    async def set_property(self, site_url: str | None) -> None:
        now = utc_now()
        async with _translate_errors(self._session):
            record = await self._session.get(GscConnectionRecord, CONNECTION_ID)
            if record is None:
                record = GscConnectionRecord(
                    id=CONNECTION_ID, state="disconnected", updated_at=now
                )
                self._session.add(record)
            record.selected_property = site_url
            record.updated_at = now
            await self._session.commit()

    # -- sync runs -------------------------------------------------------------------------

    async def create_sync(self, record: GscSyncRunRecord) -> GscSyncRunRecord:
        async with _translate_errors(self._session):
            self._session.add(record)
            await self._session.commit()
        return record

    async def get_sync(self, sync_id: UUID) -> GscSyncRunRecord | None:
        async with _translate_errors(self._session):
            return await self._session.get(GscSyncRunRecord, sync_id)

    async def refresh(self, record: Any) -> None:
        async with _translate_errors(self._session):
            await self._session.refresh(record)

    async def list_syncs(
        self, property_id: str | None, limit: int
    ) -> list[GscSyncRunRecord]:
        query = (
            select(GscSyncRunRecord)
            .order_by(GscSyncRunRecord.created_at.desc())
            .limit(limit)
        )
        if property_id:
            query = query.where(GscSyncRunRecord.property == property_id)
        async with _translate_errors(self._session):
            return list((await self._session.execute(query)).scalars().all())

    async def running_sync(self, property_id: str) -> GscSyncRunRecord | None:
        async with _translate_errors(self._session):
            return (
                await self._session.execute(
                    select(GscSyncRunRecord)
                    .where(
                        GscSyncRunRecord.property == property_id,
                        GscSyncRunRecord.status == "running",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def mark_sync_running(self, sync_id: UUID, approval: dict[str, Any]) -> bool:
        async with _translate_errors(self._session):
            result = await self._session.execute(
                update(GscSyncRunRecord)
                .where(
                    GscSyncRunRecord.id == sync_id, GscSyncRunRecord.status == "planned"
                )
                .values(status="running", approval=approval, started_at=utc_now())
            )
            await self._session.commit()
        return (getattr(result, "rowcount", 0) or 0) == 1

    async def finish_sync(
        self,
        sync_id: UUID,
        *,
        status: str,
        usage: dict[str, Any],
        error: str | None = None,
    ) -> None:
        async with _translate_errors(self._session):
            await self._session.execute(
                update(GscSyncRunRecord)
                .where(GscSyncRunRecord.id == sync_id)
                .values(
                    status=status,
                    usage=usage,
                    error_summary=error,
                    completed_at=utc_now(),
                )
            )
            await self._session.commit()

    # -- stored performance ------------------------------------------------------------------

    async def insert_daily(
        self,
        property_id: str,
        rows: Iterable[tuple[date, float, float, float, float]],
        sync_id: UUID,
    ) -> tuple[int, int]:
        """Insert per-day totals not yet stored; returns ``(inserted, skipped)``."""
        batch = list(rows)
        if not batch:
            return 0, 0
        now = utc_now()
        async with _translate_errors(self._session):
            existing = set(
                (
                    await self._session.execute(
                        select(GscDailyRecord.date).where(
                            GscDailyRecord.property == property_id,
                            GscDailyRecord.date.in_([b[0] for b in batch]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            fresh = [
                {
                    "id": uuid4(),
                    "property": property_id,
                    "date": day,
                    "clicks": clicks,
                    "impressions": impressions,
                    "ctr": ctr,
                    "position": position,
                    "sync_run_id": sync_id,
                    "fetched_at": now,
                }
                for day, clicks, impressions, ctr, position in batch
                if day not in existing
            ]
            if fresh:
                await self._session.execute(insert(GscDailyRecord), fresh)
            await self._session.commit()
        return len(fresh), len(batch) - len(fresh)

    async def insert_performance(
        self, property_id: str, rows: Sequence[dict[str, Any]], sync_id: UUID
    ) -> tuple[int, int]:
        """Insert rows whose natural key is new; existing rows are left untouched."""
        if not rows:
            return 0, 0
        now = utc_now()
        keyed = {
            performance_key(
                property_id, r["date"], r["query"], r["page"], r["search_type"]
            ): r
            for r in rows
        }
        async with _translate_errors(self._session):
            existing: set[str] = set()
            keys = list(keyed)
            for start in range(0, len(keys), KEY_CHUNK):
                chunk = keys[start : start + KEY_CHUNK]
                found = await self._session.execute(
                    select(GscPerformanceRecord.row_key).where(
                        GscPerformanceRecord.row_key.in_(chunk)
                    )
                )
                existing.update(found.scalars().all())
            fresh = [
                {
                    "id": uuid4(),
                    "row_key": key,
                    "property": property_id,
                    "date": r["date"],
                    "query": r["query"],
                    "page": r["page"],
                    "search_type": r["search_type"],
                    "clicks": r["clicks"],
                    "impressions": r["impressions"],
                    "ctr": r["ctr"],
                    "position": r["position"],
                    "sync_run_id": sync_id,
                    "fetched_at": now,
                }
                for key, r in keyed.items()
                if key not in existing
            ]
            for start in range(0, len(fresh), INSERT_CHUNK):
                await self._session.execute(
                    insert(GscPerformanceRecord), fresh[start : start + INSERT_CHUNK]
                )
            await self._session.commit()
        return len(fresh), len(keyed) - len(fresh)

    async def date_range(self, property_id: str) -> tuple[date | None, date | None]:
        async with _translate_errors(self._session):
            row = (
                await self._session.execute(
                    select(
                        func.min(GscDailyRecord.date), func.max(GscDailyRecord.date)
                    ).where(GscDailyRecord.property == property_id)
                )
            ).one()
        return row[0], row[1]

    async def days_with_data(self, property_id: str, start: date, end: date) -> int:
        async with _translate_errors(self._session):
            return int(
                (
                    await self._session.execute(
                        select(func.count())
                        .select_from(GscDailyRecord)
                        .where(
                            GscDailyRecord.property == property_id,
                            GscDailyRecord.date.between(start, end),
                        )
                    )
                ).scalar_one()
            )

    async def daily_series(
        self, property_id: str, start: date, end: date
    ) -> list[GscDailyRecord]:
        async with _translate_errors(self._session):
            return list(
                (
                    await self._session.execute(
                        select(GscDailyRecord)
                        .where(
                            GscDailyRecord.property == property_id,
                            GscDailyRecord.date.between(start, end),
                        )
                        .order_by(GscDailyRecord.date)
                    )
                )
                .scalars()
                .all()
            )

    def _agg_select(self, *group: Any) -> Any:
        sums = (
            func.sum(GscPerformanceRecord.clicks),
            func.sum(GscPerformanceRecord.impressions),
            func.sum(GscPerformanceRecord.position * GscPerformanceRecord.impressions),
        )
        return select(*group, *sums)

    async def aggregate(
        self,
        property_id: str,
        start: date,
        end: date,
        *,
        by: str,
        min_impressions: float = 0,
    ) -> list[Agg]:
        """Totals per ``page``, ``query`` or ``page_query`` over a date range."""
        columns = {
            "page": (GscPerformanceRecord.page,),
            "query": (GscPerformanceRecord.query,),
            "page_query": (GscPerformanceRecord.page, GscPerformanceRecord.query),
        }[by]
        statement = (
            self._agg_select(*columns)
            .where(
                GscPerformanceRecord.property == property_id,
                GscPerformanceRecord.date.between(start, end),
            )
            .group_by(*columns)
        )
        if min_impressions:
            statement = statement.having(
                func.sum(GscPerformanceRecord.impressions) >= min_impressions
            )
        statement = statement.order_by(
            func.sum(GscPerformanceRecord.impressions).desc()
        ).limit(MAX_AGG_ROWS)
        async with _translate_errors(self._session):
            result = (await self._session.execute(statement)).all()
        out: list[Agg] = []
        for row in result:
            values = list(row)
            clicks, impressions, weighted = (float(v or 0) for v in values[-3:])
            keys = values[:-3]
            out.append(
                Agg(
                    page=keys[0] if by in ("page", "page_query") else None,
                    query=keys[-1] if by in ("query", "page_query") else None,
                    clicks=clicks,
                    impressions=impressions,
                    position_weighted=weighted,
                )
            )
        return out

    async def seen_queries_before(self, property_id: str, before: date) -> set[str]:
        async with _translate_errors(self._session):
            rows = await self._session.execute(
                select(GscPerformanceRecord.query)
                .distinct()
                .where(
                    GscPerformanceRecord.property == property_id,
                    GscPerformanceRecord.date < before,
                )
                .limit(MAX_SEEN_QUERIES)
            )
            return set(rows.scalars().all())

    async def page_series(
        self, property_id: str, page: str, start: date, end: date
    ) -> list[Agg]:
        """Daily totals for one page (``query`` carries the ISO date)."""
        statement = (
            self._agg_select(GscPerformanceRecord.date)
            .where(
                GscPerformanceRecord.property == property_id,
                GscPerformanceRecord.page == page,
                GscPerformanceRecord.date.between(start, end),
            )
            .group_by(GscPerformanceRecord.date)
            .order_by(GscPerformanceRecord.date)
        )
        async with _translate_errors(self._session):
            result = (await self._session.execute(statement)).all()
        return [
            Agg(
                page=page,
                query=row[0].isoformat(),
                clicks=float(row[1] or 0),
                impressions=float(row[2] or 0),
                position_weighted=float(row[3] or 0),
            )
            for row in result
        ]

    async def page_queries(
        self, property_id: str, page: str, start: date, end: date, limit: int = 10
    ) -> list[Agg]:
        """Top queries (by impressions) of one page in a date range."""
        statement = (
            self._agg_select(GscPerformanceRecord.query)
            .where(
                GscPerformanceRecord.property == property_id,
                GscPerformanceRecord.page == page,
                GscPerformanceRecord.date.between(start, end),
            )
            .group_by(GscPerformanceRecord.query)
            .order_by(func.sum(GscPerformanceRecord.impressions).desc())
            .limit(limit)
        )
        async with _translate_errors(self._session):
            result = (await self._session.execute(statement)).all()
        return [
            Agg(
                page=page,
                query=row[0],
                clicks=float(row[1] or 0),
                impressions=float(row[2] or 0),
                position_weighted=float(row[3] or 0),
            )
            for row in result
        ]

    async def page_has_rows(self, property_id: str, page: str) -> bool:
        async with _translate_errors(self._session):
            found = await self._session.execute(
                select(GscPerformanceRecord.id)
                .where(
                    GscPerformanceRecord.property == property_id,
                    GscPerformanceRecord.page == page,
                )
                .limit(1)
            )
            return found.first() is not None

    # -- mappings --------------------------------------------------------------------------------

    async def upsert_mapping(
        self,
        *,
        property_id: str,
        page_url: str,
        keyword: str | None,
        article_id: UUID | None,
        article_version_id: UUID | None,
        publication_id: UUID | None,
        source: str,
        published_at: datetime | None,
    ) -> GscMappingRecord:
        pkey, kkey = page_key(page_url), keyword_key(keyword)
        async with _translate_errors(self._session):
            record = (
                await self._session.execute(
                    select(GscMappingRecord).where(
                        GscMappingRecord.property == property_id,
                        GscMappingRecord.page_key == pkey,
                        GscMappingRecord.keyword_key == kkey,
                    )
                )
            ).scalar_one_or_none()
            if record is None:
                record = GscMappingRecord(
                    id=uuid4(),
                    property=property_id,
                    page_url=normalize_page(page_url),
                    page_key=pkey,
                    keyword=keyword,
                    keyword_key=kkey,
                    source=source,
                    created_at=utc_now(),
                )
                self._session.add(record)
            record.article_id = article_id
            record.article_version_id = article_version_id
            record.publication_id = publication_id
            record.published_at = published_at
            record.active = True
            await self._session.commit()
        return record

    async def mappings(
        self, property_id: str, *, active_only: bool = True
    ) -> list[GscMappingRecord]:
        query = select(GscMappingRecord).where(GscMappingRecord.property == property_id)
        if active_only:
            query = query.where(GscMappingRecord.active.is_(True))
        async with _translate_errors(self._session):
            rows = await self._session.execute(
                query.order_by(GscMappingRecord.page_url, GscMappingRecord.keyword)
            )
            return list(rows.scalars().all())

    async def set_mapping_active(
        self, mapping_id: UUID, active: bool
    ) -> GscMappingRecord | None:
        async with _translate_errors(self._session):
            record = await self._session.get(GscMappingRecord, mapping_id)
            if record is None:
                return None
            record.active = active
            await self._session.commit()
        return record

    # -- analysis ----------------------------------------------------------------------------------

    async def save_analysis(
        self,
        *,
        property_id: str,
        status: str,
        params: dict[str, Any],
        window: dict[str, Any],
        summary: dict[str, Any],
        notes: list[str],
        insights: Sequence[InsightDraft],
        source: str = "manual",
    ) -> GscAnalysisRunRecord:
        now = utc_now()
        async with _translate_errors(self._session):
            run = GscAnalysisRunRecord(
                id=uuid4(),
                property=property_id,
                status=status,
                params=params,
                window=window,
                summary=summary,
                notes=notes,
                source=source,
                created_at=now,
            )
            self._session.add(run)
            await self._session.flush()
            for draft in insights:
                self._session.add(
                    GscInsightRecord(
                        id=uuid4(),
                        analysis_id=run.id,
                        kind=draft.kind,
                        subject_type=draft.subject_type,
                        subject=draft.subject,
                        evidence=draft.evidence,
                        impact=draft.impact,
                        confidence=draft.confidence,
                        effort=draft.effort,
                        risk=draft.risk,
                        tier=draft.tier,
                        rank=draft.rank,
                        action=draft.action,
                        rationale=draft.rationale,
                        verification=draft.verification,
                        basis=draft.basis,
                        refresh_candidate=draft.refresh_candidate,
                        article_id=UUID(draft.article_id) if draft.article_id else None,
                        status="open",
                        created_at=now,
                        updated_at=now,
                    )
                )
            await self._session.commit()
        return run

    async def get_analysis(self, analysis_id: UUID) -> GscAnalysisRunRecord | None:
        async with _translate_errors(self._session):
            return await self._session.get(GscAnalysisRunRecord, analysis_id)

    async def list_analyses(
        self, property_id: str | None, limit: int
    ) -> list[GscAnalysisRunRecord]:
        query = (
            select(GscAnalysisRunRecord)
            .order_by(GscAnalysisRunRecord.created_at.desc())
            .limit(limit)
        )
        if property_id:
            query = query.where(GscAnalysisRunRecord.property == property_id)
        async with _translate_errors(self._session):
            return list((await self._session.execute(query)).scalars().all())

    async def insights(
        self,
        analysis_id: UUID,
        *,
        kind: str | None = None,
        status: str | None = None,
        tier: str | None = None,
    ) -> list[GscInsightRecord]:
        query = select(GscInsightRecord).where(
            GscInsightRecord.analysis_id == analysis_id
        )
        if kind:
            query = query.where(GscInsightRecord.kind == kind)
        if status:
            query = query.where(GscInsightRecord.status == status)
        if tier:
            query = query.where(GscInsightRecord.tier == tier)
        async with _translate_errors(self._session):
            rows = await self._session.execute(query.order_by(GscInsightRecord.rank))
            return list(rows.scalars().all())

    async def get_insight(self, insight_id: UUID) -> GscInsightRecord | None:
        async with _translate_errors(self._session):
            return await self._session.get(GscInsightRecord, insight_id)

    async def set_insight_status(
        self, insight_id: UUID, status: str
    ) -> GscInsightRecord | None:
        async with _translate_errors(self._session):
            record = await self._session.get(GscInsightRecord, insight_id)
            if record is None:
                return None
            record.status = status
            record.updated_at = utc_now()
            await self._session.commit()
        return record

    # -- refresh tasks -------------------------------------------------------------------------------

    async def task_for_insight(self, insight_id: UUID) -> GscRefreshTaskRecord | None:
        async with _translate_errors(self._session):
            return (
                await self._session.execute(
                    select(GscRefreshTaskRecord).where(
                        GscRefreshTaskRecord.insight_id == insight_id
                    )
                )
            ).scalar_one_or_none()

    async def create_task(self, record: GscRefreshTaskRecord) -> GscRefreshTaskRecord:
        async with _translate_errors(self._session):
            self._session.add(record)
            await self._session.commit()
        return record

    async def get_task(self, task_id: UUID) -> GscRefreshTaskRecord | None:
        async with _translate_errors(self._session):
            return await self._session.get(GscRefreshTaskRecord, task_id)

    async def tasks(
        self, property_id: str | None, status: str | None
    ) -> list[GscRefreshTaskRecord]:
        query = (
            select(GscRefreshTaskRecord)
            .order_by(GscRefreshTaskRecord.created_at.desc())
            .limit(200)
        )
        if property_id:
            query = query.where(GscRefreshTaskRecord.property == property_id)
        if status:
            query = query.where(GscRefreshTaskRecord.status == status)
        async with _translate_errors(self._session):
            return list((await self._session.execute(query)).scalars().all())

    async def update_task(
        self, task_id: UUID, *, status: str, note: str | None
    ) -> GscRefreshTaskRecord | None:
        async with _translate_errors(self._session):
            record = await self._session.get(GscRefreshTaskRecord, task_id)
            if record is None:
                return None
            record.status = status
            if note is not None:
                record.note = note
            record.updated_at = utc_now()
            await self._session.commit()
        return record

    # -- schedules -----------------------------------------------------------------------------------

    async def create_schedule(self, record: GscScheduleRecord) -> GscScheduleRecord:
        async with _translate_errors(self._session):
            self._session.add(record)
            await self._session.commit()
        return record

    async def get_schedule(self, schedule_id: UUID) -> GscScheduleRecord | None:
        async with _translate_errors(self._session):
            return await self._session.get(GscScheduleRecord, schedule_id)

    async def schedules(self) -> list[GscScheduleRecord]:
        async with _translate_errors(self._session):
            rows = await self._session.execute(
                select(GscScheduleRecord).order_by(GscScheduleRecord.created_at)
            )
            return list(rows.scalars().all())

    async def due_schedules(self, now: datetime) -> list[GscScheduleRecord]:
        async with _translate_errors(self._session):
            rows = await self._session.execute(
                select(GscScheduleRecord).where(
                    GscScheduleRecord.enabled.is_(True),
                    GscScheduleRecord.approved_at.is_not(None),
                    GscScheduleRecord.next_run_at.is_not(None),
                    GscScheduleRecord.next_run_at <= now,
                )
            )
            return list(rows.scalars().all())

    async def save(self, record: Any) -> None:
        """Commit changes made to an already-loaded record."""
        async with _translate_errors(self._session):
            await self._session.commit()
