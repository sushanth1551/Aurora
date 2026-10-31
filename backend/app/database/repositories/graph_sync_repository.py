"""PostgreSQL side of graph synchronization: outbox, run metrics, resolutions.

PostgreSQL and Neo4j share no transaction. The outbox records *intent* to
project a research job; it is marked ``succeeded`` only after Neo4j confirmed
every batch, and ``failed`` (with an exponential ``next_retry_at``) otherwise.
After ``max_attempts`` the event becomes ``dead`` until reset by reconciliation
or a manual sync. Each method commits its own unit of work.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    GraphEntityResolutionRecord,
    GraphSyncOutboxRecord,
    GraphSyncRunRecord,
    ResearchClaimRecord,
    ResearchEntityRecord,
)
from app.database.repositories.research_repository import _translate_errors
from app.graph.graph_schema import Label
from app.graph.models import ResolutionDecision, ResolutionRecord, SyncRun
from app.schemas.research import utc_now

AGGREGATE = "research_job"
OPERATION = "project_job"
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 3600


class GraphSyncRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- outbox ----------------------------------------------------------------------------

    async def get_event(self, job_id: UUID) -> GraphSyncOutboxRecord | None:
        async with _translate_errors(self._session):
            return (
                await self._session.execute(
                    select(GraphSyncOutboxRecord).where(
                        GraphSyncOutboxRecord.aggregate_type == AGGREGATE,
                        GraphSyncOutboxRecord.aggregate_id == job_id,
                        GraphSyncOutboxRecord.operation == OPERATION,
                    )
                )
            ).scalar_one_or_none()

    async def enqueue(
        self, job_id: UUID, *, reset: bool = True
    ) -> GraphSyncOutboxRecord:
        """Ensure one pending event for the job (idempotent)."""
        now = utc_now()
        async with _translate_errors(self._session):
            event = await self.get_event(job_id)
            if event is None:
                event = GraphSyncOutboxRecord(
                    event_id=uuid4(), aggregate_type=AGGREGATE, aggregate_id=job_id, operation=OPERATION,
                    payload_reference=f"research_jobs/{job_id}", status="pending", attempt_count=0,
                    created_at=now, updated_at=now,
                )  # fmt: skip
                self._session.add(event)
            elif reset or event.status in ("succeeded", "pending"):
                event.status = "pending"
                if reset:
                    event.attempt_count = 0
                    event.next_retry_at = None
                event.updated_at = now
            await self._session.commit()
            return event

    async def mark_processing(self, job_id: UUID) -> GraphSyncOutboxRecord:
        now = utc_now()
        async with _translate_errors(self._session):
            event = await self.get_event(job_id)
            assert event is not None
            event.status = "processing"
            event.attempt_count += 1
            event.updated_at = now
            await self._session.commit()
            return event

    async def mark_succeeded(self, job_id: UUID) -> None:
        now = utc_now()
        async with _translate_errors(self._session):
            event = await self.get_event(job_id)
            if event is not None:
                event.status = "succeeded"
                event.last_error = None
                event.next_retry_at = None
                event.processed_at = now
                event.updated_at = now
                await self._session.commit()

    async def mark_failed(
        self, job_id: UUID, error: str, *, max_attempts: int
    ) -> GraphSyncOutboxRecord | None:
        now = utc_now()
        async with _translate_errors(self._session):
            event = await self.get_event(job_id)
            if event is None:
                return None
            event.last_error = error[:500]
            event.updated_at = now
            if event.attempt_count >= max_attempts:
                event.status = "dead"
                event.next_retry_at = None
            else:
                event.status = "failed"
                delay = min(
                    RETRY_MAX_SECONDS,
                    RETRY_BASE_SECONDS * 2 ** max(0, event.attempt_count - 1),
                )
                event.next_retry_at = now + timedelta(seconds=delay)
            await self._session.commit()
            return event

    async def due_events(
        self, *, limit: int, max_attempts: int, include_pending: bool = True
    ) -> list[GraphSyncOutboxRecord]:
        now = utc_now()
        statuses = ["failed", "pending"] if include_pending else ["failed"]
        async with _translate_errors(self._session):
            rows = (
                await self._session.execute(
                    select(GraphSyncOutboxRecord)
                    .where(
                        GraphSyncOutboxRecord.status.in_(statuses),
                        GraphSyncOutboxRecord.attempt_count < max_attempts,
                        or_(GraphSyncOutboxRecord.next_retry_at.is_(None), GraphSyncOutboxRecord.next_retry_at <= now),
                    )
                    .order_by(GraphSyncOutboxRecord.created_at)
                    .limit(limit)
                )
            ).scalars().all()  # fmt: skip
        return list(rows)

    async def events_by_job(self) -> dict[UUID, GraphSyncOutboxRecord]:
        async with _translate_errors(self._session):
            rows = (
                (await self._session.execute(select(GraphSyncOutboxRecord)))
                .scalars()
                .all()
            )
        return {r.aggregate_id: r for r in rows}

    # -- eligibility (source of truth) ---------------------------------------------------------

    async def eligible_jobs(self, limit: int = 10_000) -> dict[UUID, int]:
        """Jobs with any entity or claim -> number of claims stored in PostgreSQL."""
        async with _translate_errors(self._session):
            ids = union(
                select(ResearchClaimRecord.research_job_id),
                select(ResearchEntityRecord.research_job_id),
            ).subquery()
            job_ids = [
                r[0]
                for r in (
                    await self._session.execute(select(ids.c[0]).limit(limit))
                ).all()
            ]
            counts = dict(
                (
                    await self._session.execute(
                        select(ResearchClaimRecord.research_job_id, func.count())
                        .where(ResearchClaimRecord.research_job_id.in_(job_ids))
                        .group_by(ResearchClaimRecord.research_job_id)
                    )
                ).all()
            )
        return {j: int(counts.get(j, 0)) for j in job_ids}

    # -- runs ----------------------------------------------------------------------------------

    async def save_run(self, run: SyncRun) -> None:
        async with _translate_errors(self._session):
            record = await self._session.get(GraphSyncRunRecord, run.run_id)
            data: dict[str, Any] = {
                "research_job_id": run.research_job_id,
                "mode": run.mode,
                "dry_run": run.dry_run,
                "status": run.status,
                "counts": run.counts.model_dump(),
                "rejected": [r.model_dump(mode="json") for r in run.rejected[:200]],
                "review": [r.model_dump(mode="json") for r in run.review[:200]],
                "error_summary": run.error,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "duration_ms": run.duration_ms,
            }
            if record is None:
                self._session.add(GraphSyncRunRecord(id=run.run_id, **data))
            else:
                for k, v in data.items():
                    setattr(record, k, v)
            await self._session.commit()

    async def runs_for_job(
        self, job_id: UUID, limit: int = 10
    ) -> list[GraphSyncRunRecord]:
        async with _translate_errors(self._session):
            rows = (
                await self._session.execute(
                    select(GraphSyncRunRecord)
                    .where(GraphSyncRunRecord.research_job_id == job_id)
                    .order_by(GraphSyncRunRecord.started_at.desc())
                    .limit(limit)
                )
            ).scalars().all()  # fmt: skip
        return list(rows)

    # -- resolutions -----------------------------------------------------------------------------

    async def prior_resolutions(
        self,
        job_id: UUID,
        *,
        scopes: set[str],
        match_keys: set[str],
        entity_keys: set[str],
    ) -> list[ResolutionRecord]:
        """Resolutions from *other* jobs that could match this job's entities."""
        scope_free = [
            Label.TECHNOLOGY.value,
            Label.INTEGRATION.value,
            Label.TOPIC.value,
        ]
        async with _translate_errors(self._session):
            rows = (
                await self._session.execute(
                    select(GraphEntityResolutionRecord).where(
                        GraphEntityResolutionRecord.research_job_id != job_id,
                        or_(
                            GraphEntityResolutionRecord.entity_key.in_(entity_keys or {""}),
                            GraphEntityResolutionRecord.scope_domain.in_(scopes or {""}),
                            GraphEntityResolutionRecord.label.in_(scope_free)
                            & GraphEntityResolutionRecord.match_key.in_(match_keys or {""}),
                            GraphEntityResolutionRecord.match_key.in_(match_keys or {""})
                            & (GraphEntityResolutionRecord.label == Label.COMPANY.value),
                        ),
                    ).order_by(GraphEntityResolutionRecord.created_at)
                )
            ).scalars().all()  # fmt: skip
        seen: dict[str, ResolutionRecord] = {}
        for r in rows:
            seen.setdefault(r.entity_key, self._to_model(r))
        return list(seen.values())

    async def save_resolutions(
        self, job_id: UUID, records: list[ResolutionRecord]
    ) -> None:
        now = utc_now()
        async with _translate_errors(self._session):
            existing = {
                r.entity_key: r
                for r in (
                    await self._session.execute(
                        select(GraphEntityResolutionRecord).where(
                            GraphEntityResolutionRecord.research_job_id == job_id
                        )
                    )
                ).scalars()
            }
            for rec in records:
                data = {
                    "label": rec.label.value, "graph_node_id": rec.graph_node_id, "stage": rec.stage,
                    "decision": rec.decision.value, "confidence": rec.confidence, "reasons": rec.reasons,
                    "candidate_node_id": rec.candidate_node_id, "canonical_name": rec.canonical_name,
                    "match_key": rec.match_key[:300], "scope_domain": rec.scope_domain, "aliases": rec.aliases[:100],
                    "updated_at": now,
                }  # fmt: skip
                row = existing.get(rec.entity_key)
                if row is None:
                    self._session.add(
                        GraphEntityResolutionRecord(
                            id=uuid4(),
                            research_job_id=job_id,
                            entity_key=rec.entity_key,
                            created_at=now,
                            **data,
                        )
                    )
                else:
                    for k, v in data.items():
                        setattr(row, k, v)
            await self._session.commit()

    async def review_candidates(self, limit: int = 100) -> list[ResolutionRecord]:
        async with _translate_errors(self._session):
            rows = (
                await self._session.execute(
                    select(GraphEntityResolutionRecord)
                    .where(GraphEntityResolutionRecord.decision == ResolutionDecision.REVIEW.value)
                    .order_by(GraphEntityResolutionRecord.updated_at.desc())
                    .limit(limit)
                )
            ).scalars().all()  # fmt: skip
        return [self._to_model(r) for r in rows]

    @staticmethod
    def _to_model(r: GraphEntityResolutionRecord) -> ResolutionRecord:
        return ResolutionRecord(
            research_job_id=str(r.research_job_id), entity_key=r.entity_key, label=Label(r.label),
            graph_node_id=r.graph_node_id, stage=r.stage, decision=ResolutionDecision(r.decision),
            confidence=r.confidence, reasons=list(r.reasons or []), candidate_node_id=r.candidate_node_id,
            canonical_name=r.canonical_name, match_key=r.match_key, scope_domain=r.scope_domain,
            aliases=list(r.aliases or []),
        )  # fmt: skip
