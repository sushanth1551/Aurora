"""PostgreSQL -> Neo4j projection with an idempotent outbox (eventual consistency).

Per research job:

1. load stored entities, claims, sources and websites (PostgreSQL is the source
   of truth; nothing is fetched from the web);
2. extract graph entities, Source and Claim nodes;
3. resolve identities against earlier syncs (``graph_entity_resolutions``);
4. extract and validate relationships, build SUPPORTS/MENTIONS provenance;
5. dry run: stop here and report what *would* be written;
6. otherwise mark the outbox event ``processing`` and write nodes, then
   relationships, in bounded batches inside write transactions, retrying
   transient errors (``GraphUnavailableError``) with backoff;
7. only after every batch is confirmed: save resolutions, mark the event
   ``succeeded`` and record run metrics. Any failure marks it ``failed`` with a
   ``next_retry_at`` (or ``dead`` after ``GRAPH_SYNC_MAX_RETRIES``).

Writes are MERGE-by-id upserts, so re-running a job (or a crash between a graph
write and the PostgreSQL commit) converges to the same graph without duplicates.
No PostgreSQL record is modified except the three sync bookkeeping tables.
"""

import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.exceptions import ResourceNotFoundException
from app.core.logging import log_event
from app.database.repositories.graph_sync_repository import GraphSyncRepository
from app.database.repositories.intelligence_repository import IntelligenceRepository
from app.database.repositories.research_repository import ResearchRepository
from app.graph.entity_extractor import EntityExtractor
from app.graph.entity_resolver import GraphEntityResolver
from app.graph.graph_exceptions import GraphNotConfiguredError, GraphSyncDisabledError
from app.graph.graph_repository import (
    GraphRepository,
    claim_row,
    edge_row,
    entity_row,
    source_row,
)
from app.graph.graph_schema import Label
from app.graph.models import GraphProjection, ResolutionDecision, SyncRun
from app.graph.provenance_manager import build_provenance
from app.graph.relationship_extractor import RelationshipExtractor
from app.schemas.research import utc_now
from app.tools.errors import ProviderError
from app.tools.http import retry_async

logger = logging.getLogger(__name__)


class ReconcileItem(BaseModel):
    research_job_id: UUID
    postgres_claims: int
    graph_claims: int | None
    outbox_status: str | None
    state: str  # in_sync | missing | stale | failed | dead | pending | unknown
    action: str = "none"


class ReconcileReport(BaseModel):
    dry_run: bool
    graph_checked: bool
    items: list[ReconcileItem] = Field(default_factory=list)
    enqueued: int = 0
    totals: dict[str, int] = Field(default_factory=dict)


class GraphSyncService:
    def __init__(
        self,
        *,
        research_repo: ResearchRepository,
        intelligence_repo: IntelligenceRepository,
        sync_repo: GraphSyncRepository,
        graph: GraphRepository | None,
        settings: Settings | None = None,
        resolver: GraphEntityResolver | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._research = research_repo
        self._intel = intelligence_repo
        self._sync = sync_repo
        self._graph = graph
        self._resolver = resolver or GraphEntityResolver(
            llm_min_confidence=self._settings.graph_llm_resolution_min_confidence
        )
        self._extractor = EntityExtractor()
        self._relationships = RelationshipExtractor()
        self._sleep = sleep

    # -- projection (pure; no graph access) --------------------------------------------------

    async def project(self, job_id: UUID) -> GraphProjection:
        response = await self._research.get_response(job_id)
        if response is None:
            raise ResourceNotFoundException(f"Research job {job_id} was not found.")
        websites, entities, claims = await self._intel.load_material(job_id)
        jid = str(job_id)
        ex = self._extractor.extract(jid, websites, entities, claims, response.sources)
        prior = await self._sync.prior_resolutions(
            job_id,
            scopes={e.scope_domain for e in ex.entities.values() if e.scope_domain},
            match_keys={e.match_key for e in ex.entities.values()},
            entity_keys=set(ex.entities),
        )
        evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in ex.claims:
            subject, _ = ex.claim_links.get(c.id, (None, None))
            if subject:
                evidence[subject].append(
                    {
                        "claim_id": c.id,
                        "predicate": c.predicate,
                        "text": c.claim_text[:200],
                    }
                )
        mapping, nodes, resolutions = await self._resolver.resolve(
            jid,
            ex.entities.values(),
            verified_domains=ex.verified_domains,
            prior=prior,
            claim_evidence=evidence,
        )
        links = {
            cid: (mapping.get(s or "", s), mapping.get(o or "", o))
            for cid, (s, o) in ex.claim_links.items()
        }
        node_map = {n.id: n for n in nodes}
        for n in nodes:  # products/services point at their (resolved) company
            if n.label in (Label.PRODUCT, Label.SERVICE) and n.scope_domain:
                owner = next(
                    (
                        m.id
                        for m in nodes
                        if m.label is Label.COMPANY and m.scope_domain == n.scope_domain
                    ),
                    None,
                )
                if owner:
                    n.properties.setdefault("company_id", owner)
        rels, rejected = self._relationships.extract(ex.claims, links, node_map)
        provenance, _linked = build_provenance(ex.claims, links, node_map)
        return GraphProjection(
            research_job_id=jid,
            entities=nodes,
            sources=list(ex.sources.values()),
            claims=ex.claims,
            relationships=rels,
            provenance=provenance,
            rejected=rejected,
            resolutions=resolutions,
        )

    # -- per job --------------------------------------------------------------------------------

    async def sync_job(
        self, job_id: UUID, *, dry_run: bool = False, mode: str = "job"
    ) -> SyncRun:
        if not self._settings.graph_sync_enabled:
            raise GraphSyncDisabledError(
                "Graph synchronization is disabled (GRAPH_SYNC_ENABLED=false)."
            )
        started = time.monotonic()
        run = SyncRun(research_job_id=job_id, mode=mode, dry_run=dry_run)
        projection = await self.project(job_id)
        self._plan_counts(run, projection)
        if dry_run:
            run.status = "dry_run"
            return self._finish(run, started)
        if self._graph is None:
            raise GraphNotConfiguredError(
                "Neo4j is not configured; set NEO4J_PASSWORD."
            )
        await self._sync.enqueue(job_id, reset=False)
        await self._sync.mark_processing(job_id)
        await self._sync.save_run(run)
        try:
            await self._write(projection, run)
        except ProviderError as exc:
            run.status = "failed"
            run.error = exc.code
            await self._sync.mark_failed(
                job_id, exc.code, max_attempts=self._settings.graph_sync_max_retries + 1
            )
            await self._sync.save_run(self._finish(run, started))
            log_event(
                logger,
                "graph_sync_failed",
                level=logging.WARNING,
                job_id=job_id,
                error=exc.code,
            )
            raise
        await self._sync.save_resolutions(job_id, projection.resolutions)
        await self._sync.mark_succeeded(job_id)
        run.status = "partially_completed" if run.counts.failed_records else "completed"
        await self._sync.save_run(self._finish(run, started))
        log_event(
            logger,
            "graph_sync_completed",
            job_id=job_id,
            run_id=run.run_id,
            **run.counts.model_dump(),
        )
        return run

    @staticmethod
    def _plan_counts(run: SyncRun, p: GraphProjection) -> None:
        c = run.counts
        c.entities_processed = len(p.resolutions) + sum(
            e.derived_from_claim for e in p.entities
        )
        c.duplicates_detected = sum(
            r.decision is ResolutionDecision.MERGED for r in p.resolutions
        )
        c.review_candidates = sum(
            r.decision is ResolutionDecision.REVIEW for r in p.resolutions
        )
        c.relationships_rejected = len(p.rejected)
        c.claims_linked = len(
            {r.source_id for r in p.provenance if r.type.value == "MENTIONS"}
        )
        run.rejected = p.rejected
        run.review = [
            r for r in p.resolutions if r.decision is ResolutionDecision.REVIEW
        ]

    async def _write(self, p: GraphProjection, run: SyncRun) -> None:
        assert self._graph is not None
        size = self._settings.graph_sync_batch_size
        by_label: dict[Label, list[dict[str, Any]]] = defaultdict(list)
        for e in p.entities:
            by_label[e.label].append(entity_row(e))
        groups: list[tuple[Label, list[dict[str, Any]], str]] = [
            *[(lab, rows, "entities") for lab, rows in sorted(by_label.items())],
            (Label.SOURCE, [source_row(s) for s in p.sources], "sources"),
            (Label.CLAIM, [claim_row(c) for c in p.claims], "claims"),
        ]
        graph = self._graph
        for lab, rows, kind in groups:
            for i in range(0, len(rows), size):
                created, updated = await self._retry(
                    partial(graph.upsert_nodes, lab, rows[i : i + size]), run
                )
                c = run.counts
                if kind == "entities":
                    c.entities_created += created
                    c.entities_updated += updated
                elif kind == "sources":
                    c.source_nodes_created += created
                    c.source_nodes_updated += updated
                else:
                    c.claim_nodes_created += created
                    c.claim_nodes_updated += updated
        edge_groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        for r in [*p.relationships, *p.provenance]:
            edge_groups[(r.type, r.source_label, r.target_label)].append(edge_row(r))
        for (rel, src, dst), rows in sorted(
            edge_groups.items(), key=lambda kv: [str(x) for x in kv[0]]
        ):
            for i in range(0, len(rows), size):
                created, updated, missing = await self._retry(
                    partial(graph.upsert_edges, rel, src, dst, rows[i : i + size]), run
                )
                run.counts.relationships_created += created
                run.counts.relationships_updated += updated
                run.counts.failed_records += missing

    async def _retry(self, op: Callable[[], Awaitable[Any]], run: SyncRun) -> Any:
        attempts = 0

        async def counted() -> Any:
            nonlocal attempts
            attempts += 1
            return await op()

        kwargs: dict[str, Any] = {"sleep": self._sleep} if self._sleep else {}
        try:
            return await retry_async(
                counted, provider="neo4j", max_retries=self._settings.graph_sync_max_retries,
                base_delay=0.5, max_delay=8.0, **kwargs,
            )  # fmt: skip
        finally:
            run.counts.retries += max(0, attempts - 1)

    @staticmethod
    def _finish(run: SyncRun, started: float) -> SyncRun:
        run.completed_at = utc_now()
        run.duration_ms = int((time.monotonic() - started) * 1000)
        return run

    # -- batches -----------------------------------------------------------------------------------

    async def process_due(
        self, *, limit: int = 20, include_pending: bool = True
    ) -> list[SyncRun]:
        """Incremental sync (pending events) and failed-event retry."""
        events = await self._sync.due_events(
            limit=limit,
            max_attempts=self._settings.graph_sync_max_retries + 1,
            include_pending=include_pending,
        )
        runs: list[SyncRun] = []
        for event in events:
            try:
                runs.append(
                    await self.sync_job(
                        event.aggregate_id,
                        mode="retry" if event.status == "failed" else "incremental",
                    )
                )
            except ProviderError as exc:
                runs.append(
                    SyncRun(
                        research_job_id=event.aggregate_id,
                        mode="retry",
                        status="failed",
                        error=exc.code,
                    )
                )
            except ResourceNotFoundException:
                await self._sync.mark_failed(
                    event.aggregate_id, "research job no longer exists", max_attempts=0
                )
        return runs

    async def full_sync(
        self, *, dry_run: bool = False, limit: int = 1000
    ) -> list[SyncRun]:
        jobs = list((await self._sync.eligible_jobs(limit=limit)).keys())
        runs = []
        for job in jobs:
            try:
                runs.append(await self.sync_job(job, dry_run=dry_run, mode="full"))
            except ProviderError as exc:
                runs.append(
                    SyncRun(
                        research_job_id=job,
                        mode="full",
                        status="failed",
                        error=exc.code,
                    )
                )
        return runs

    async def reconcile(self, *, dry_run: bool = True) -> ReconcileReport:
        """Compare PostgreSQL claims with graph claims per job; enqueue drift."""
        eligible = await self._sync.eligible_jobs()
        events = await self._sync.events_by_job()
        report = ReconcileReport(dry_run=dry_run, graph_checked=self._graph is not None)
        for job_id, pg_claims in sorted(eligible.items(), key=lambda kv: str(kv[0])):
            event = events.get(job_id)
            graph_claims = None
            if self._graph is not None:
                graph_claims = await self._graph.count_job_claims(str(job_id))
            status = event.status if event else None
            if event is None:
                state = "missing"
            elif status in ("failed", "dead", "pending", "processing"):
                state = status
            elif graph_claims is not None and graph_claims < pg_claims:
                state = "stale"
            elif graph_claims is None:
                state = "unknown"
            else:
                state = "in_sync"
            item = ReconcileItem(
                research_job_id=job_id, postgres_claims=pg_claims, graph_claims=graph_claims,
                outbox_status=status, state=state,
            )  # fmt: skip
            if state in (
                "missing",
                "stale",
                "dead",
                "failed",
                "processing",
            ):  # processing = interrupted run
                item.action = "would_enqueue" if dry_run else "enqueued"
                if not dry_run:
                    await self._sync.enqueue(job_id, reset=True)
                    report.enqueued += 1
            report.items.append(item)
        totals: dict[str, int] = defaultdict(int)
        for item in report.items:
            totals[item.state] += 1
        report.totals = dict(totals)
        log_event(
            logger,
            "graph_reconciled",
            dry_run=dry_run,
            jobs=len(report.items),
            enqueued=report.enqueued,
            **report.totals,
        )
        return report
