"""Keyword research service: plan -> approve and run -> stored results.

- ``plan`` validates inputs, reads topics from the site's latest stored technical
  audit (if any) and stores a ``planned`` run with an upper-bound estimate. It
  makes **no** SerpApi request.
- ``run`` needs explicit approval with a search ceiling no higher than the
  estimate. It reuses fresh stored SERP snapshots before spending a search,
  spaces searches by ``SEO_SERP_MIN_INTERVAL_SECONDS``, and stops at the search
  budget, the time limit, or three consecutive provider failures. Results
  (intent, clusters, competitors, gaps, opportunities) are stored in one
  transaction. Runs are synchronous.
- Only SerpApi is used. Search volume, difficulty and CPC are not provided by it
  and stay ``not_available``.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.core.config import Settings
from app.core.exceptions import (
    ConflictException,
    ResearchException,
    ResourceNotFoundException,
)
from app.database.models import SeoKeywordRunRecord, SeoSerpSnapshotRecord
from app.database.repositories.keyword_repository import KeywordRepository
from app.database.repositories.seo_audit_repository import SeoAuditRepository
from app.research.url_policy import UrlPolicy, UrlRejected
from app.schemas.research import SearchResult, utc_now
from app.seo.keywords.analysis import (
    Snapshot,
    build_opportunities,
    cluster_keywords,
    detect_gaps,
    find_competitors,
)
from app.seo.keywords.discovery import (
    CandidatePool,
    serp_discoveries,
    site_topic_phrases,
    template_expansions,
)
from app.seo.keywords.intent import brand_tokens_for, classify
from app.seo.keywords.models import (
    ResearchEstimate,
    ResearchLimits,
    ResearchUsage,
    as_dict,
)
from app.seo.keywords.text import bare_host, normalize_keyword
from app.tools.errors import ProviderError
from app.tools.serpapi_features import SerpFeatures, SerpSnapshotData

logger = logging.getLogger(__name__)

DEVICES = ("desktop", "mobile", "tablet")
MAX_SITE_TEXT = 800
MAX_COMPETITORS_STORED = 30
MAX_CONSECUTIVE_FAILURES = 3


class SerpSearcher(Protocol):
    async def search_serp(
        self,
        query: str,
        *,
        num_results: int = ...,
        country: str | None = ...,
        language: str | None = ...,
        device: str | None = ...,
        location: str | None = ...,
    ) -> SerpSnapshotData: ...


def snapshot_from_record(record: SeoSerpSnapshotRecord) -> SerpSnapshotData:
    return SerpSnapshotData(
        query=record.query,
        country=record.country,
        language=record.language,
        device=record.device,
        location=record.location,
        provider_search_id=record.provider_search_id,
        organic=[SearchResult.model_validate(o) for o in record.organic],
        features=SerpFeatures.model_validate(record.features),
    )


class KeywordResearchService:
    def __init__(
        self,
        *,
        repository: KeywordRepository,
        audit_repository: SeoAuditRepository,
        settings: Settings,
        policy: UrlPolicy,
        serp: SerpSearcher,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repo = repository
        self._audits = audit_repository
        self._settings = settings
        self._policy = policy
        self._serp = serp
        self._sleep = sleep
        self._clock = clock

    @property
    def repository(self) -> KeywordRepository:
        return self._repo

    # -- site text from the latest stored audit -------------------------------------

    async def _site_text(self, host: str) -> tuple[str | None, list[str], list[str]]:
        """``(audit_id, topic_sources, all_text)`` from stored audit pages (no network)."""
        audit = await self._audits.latest_completed(host)
        if audit is None:
            return None, [], []
        rows, _ = await self._audits.pages(audit.id, limit=200, offset=0)
        topic_sources: list[str] = []
        all_text: list[str] = []
        for row in rows:
            signals = row.signals or {}
            titles = [t for t in signals.get("titles", []) if isinstance(t, str)]
            headings = [h for h in signals.get("headings", []) if isinstance(h, dict)]
            topic_sources += titles[:1]
            topic_sources += [
                h["text"]
                for h in headings
                if h.get("level") in (1, 2) and h.get("text")
            ]
            all_text += titles + [h["text"] for h in headings if h.get("text")]
        return str(audit.id), topic_sources, all_text[:MAX_SITE_TEXT]

    # -- plan -------------------------------------------------------------------------

    def _limits(
        self, max_queries: int | None, include_expansions: bool
    ) -> ResearchLimits:
        s = self._settings
        if max_queries is not None and max_queries > s.seo_keyword_max_queries:
            raise ResearchException(
                f"max_queries may not exceed the configured maximum of {s.seo_keyword_max_queries}."
            )
        return ResearchLimits(
            max_queries=max_queries or s.seo_keyword_max_queries,
            max_candidates=s.seo_keyword_max_candidates,
            max_seeds=s.seo_keyword_max_seeds,
            include_expansions=include_expansions,
            cache_ttl_hours=s.seo_serp_cache_ttl_hours,
            min_interval_seconds=s.seo_serp_min_interval_seconds,
        )

    async def plan(
        self,
        site_url: str,
        *,
        seeds: list[str],
        country: str | None = None,
        language: str | None = None,
        device: str | None = None,
        location: str | None = None,
        max_queries: int | None = None,
        include_expansions: bool = False,
    ) -> SeoKeywordRunRecord:
        try:
            normalized_url, host = self._policy.normalize(site_url)
        except UrlRejected as exc:
            raise ResearchException(f"The URL is not allowed: {exc.message}") from exc
        if device is not None and device not in DEVICES:
            raise ResearchException(f"device must be one of {', '.join(DEVICES)}.")
        limits = self._limits(max_queries, include_expansions)
        clean_seeds: list[str] = []
        for seed in seeds:
            text = " ".join(seed.split())
            if len(text) >= 2 and normalize_keyword(text) not in {
                normalize_keyword(s) for s in clean_seeds
            }:
                clean_seeds.append(text)
        if len(clean_seeds) > limits.max_seeds:
            raise ResearchException(
                f"At most {limits.max_seeds} seed keywords are allowed."
            )
        site = bare_host(host)
        audit_id, topic_sources, _ = await self._site_text(site)
        topics = site_topic_phrases(topic_sources, limit=limits.max_queries)
        if not clean_seeds and not topics:
            raise ResearchException(
                "Provide at least one seed keyword, or run a technical SEO audit of this site "
                "first so topics can be taken from its pages."
            )
        planned = list(dict.fromkeys(clean_seeds + topics))
        if include_expansions:
            planned += [e for s in clean_seeds for e in template_expansions(s)]
        planned = list(dict.fromkeys(planned))[: limits.max_queries]

        cached = 0
        for query in planned:
            if await self._repo.fresh_snapshot(
                normalize_keyword(query),
                country=country,
                language=language,
                device=device,
                location=location,
                ttl_hours=limits.cache_ttl_hours,
            ):
                cached += 1
        per_search = self._settings.seo_serpapi_cost_per_search
        estimate = ResearchEstimate(
            serpapi_searches_max=limits.max_queries,
            cached_snapshots_possible=cached,
            estimated_cost=None
            if per_search is None
            else round(per_search * limits.max_queries, 4),
            cost_note=(
                "Upper bound at the configured price per search; cached snapshots cost nothing."
                if per_search is not None
                else "Cost per search is not configured (SEO_SERPAPI_COST_PER_SEARCH); no cost is estimated."
            ),
            notes=[
                "Only SerpApi is used: one search credit per uncached query. No LLM and no Firecrawl.",
                "Beyond the planned queries, People Also Ask questions and related searches found "
                "on fetched SERPs are searched until the approved ceiling is reached.",
                "SerpApi returns no search volume, difficulty or CPC; those stay unavailable.",
            ],
        )
        record = SeoKeywordRunRecord(
            id=uuid4(),
            site_url=normalized_url,
            host=site,
            status="planned",
            params={
                "seeds": clean_seeds,
                "site_topics": topics,
                "audit_id": audit_id,
                "country": country,
                "language": language,
                "device": device,
                "location": location,
                "planned_queries": planned,
                "include_expansions": include_expansions,
            },
            limits=limits.model_dump(),
            estimate=estimate.model_dump(),
            approval=None,
            usage={},
            summary={},
            competitors=[],
            gaps=[],
            gap_status=None,
            created_at=utc_now(),
        )
        return await self._repo.create_run(record)

    # -- run --------------------------------------------------------------------------

    async def get(self, run_id: UUID, *, refresh: bool = False) -> SeoKeywordRunRecord:
        run = await self._repo.get_run(run_id)
        if run is None:
            raise ResourceNotFoundException("Keyword research run not found.")
        if refresh:
            await self._repo.refresh(run)
        return run

    async def run(self, run_id: UUID, *, max_searches: int) -> SeoKeywordRunRecord:
        run = await self.get(run_id)
        if run.status != "planned":
            raise ConflictException(
                f"Run is '{run.status}'; only a planned run can be started."
            )
        estimate = ResearchEstimate.model_validate(run.estimate)
        if max_searches > estimate.serpapi_searches_max:
            raise ResearchException(
                f"max_searches ({max_searches}) exceeds the plan's maximum "
                f"({estimate.serpapi_searches_max}); create a new plan for a larger run."
            )
        ensure = getattr(self._serp, "ensure_configured", None)
        if callable(ensure):
            ensure()  # 503 before anything is marked or spent
        if await self._repo.running_for_host(run.host) is not None:
            raise ConflictException(
                "Another keyword research run for this site is already running."
            )
        approval = {"approved_at": utc_now().isoformat(), "max_searches": max_searches}
        if not await self._repo.mark_running(run_id, approval):
            raise ConflictException("The run was started by another request.")

        usage = ResearchUsage()
        try:
            outcome = await self._execute(run, max_searches, usage)
        except Exception as exc:  # noqa: BLE001 - recorded, never leaked
            logger.error(
                "keyword_run_failed run_id=%s error=%s", run_id, type(exc).__name__
            )
            await self._repo.mark_failed(
                run_id, "The run failed unexpectedly.", as_dict(usage)
            )
            return await self.get(run_id, refresh=True)
        await self._repo.save_results(run_id, **outcome)
        return await self.get(run_id, refresh=True)

    async def _execute(
        self, run: SeoKeywordRunRecord, max_searches: int, usage: ResearchUsage
    ) -> dict[str, Any]:
        params = run.params
        limits = ResearchLimits.model_validate(run.limits)
        country, language = params.get("country"), params.get("language")
        device, location = params.get("device"), params.get("location")
        deadline = self._clock() + self._settings.seo_keyword_max_duration_seconds
        per_search = self._settings.seo_serpapi_cost_per_search

        pool = CandidatePool(limits.max_candidates)
        pool.add_many(params["seeds"], "seed")
        pool.add_many(params["site_topics"], "site_topic", params.get("audit_id"))
        if limits.include_expansions:
            for seed in params["seeds"]:
                pool.add_many(template_expansions(seed), "template", seed)
        queue = [c.normalized for c in pool.all()]
        snaps: dict[str, Snapshot] = {}
        snapshot_ids: dict[str, UUID] = {}
        failures_in_a_row = 0
        first_search = True
        unsearched_priority = False

        while queue:
            normalized = queue.pop(0)
            if normalized in snaps:
                continue
            candidate = pool.get(normalized)
            if candidate is None:
                continue
            cached = await self._repo.fresh_snapshot(
                normalized,
                country=country,
                language=language,
                device=device,
                location=location,
                ttl_hours=limits.cache_ttl_hours,
            )
            if cached is not None:
                usage.cache_hits += 1
                data, snapshot_id, hit = snapshot_from_record(cached), cached.id, True
            else:
                if usage.serpapi_searches >= max_searches:
                    usage.stopped_reason = usage.stopped_reason or "search_budget"
                    unsearched_priority = unsearched_priority or candidate.source in (
                        "seed",
                        "site_topic",
                    )
                    continue
                if self._clock() >= deadline:
                    usage.stopped_reason = "deadline"
                    break
                if not first_search and limits.min_interval_seconds > 0:
                    await self._sleep(limits.min_interval_seconds)
                first_search = False
                try:
                    data = await self._serp.search_serp(
                        candidate.keyword,
                        country=country,
                        language=language,
                        device=device,
                        location=location,
                    )
                except ProviderError as exc:
                    usage.failures += 1
                    failures_in_a_row += 1
                    logger.warning("keyword_search_failed code=%s", exc.code)
                    if failures_in_a_row >= MAX_CONSECUTIVE_FAILURES:
                        usage.stopped_reason = "provider_failures"
                        break
                    continue
                failures_in_a_row = 0
                usage.serpapi_searches += (
                    1  # counted when SerpApi answered (a charged search)
                )
                snapshot_id, hit = (
                    await self._repo.save_snapshot(data, normalized),
                    False,
                )
            snaps[normalized] = Snapshot(str(snapshot_id), data, cache_hit=hit)
            snapshot_ids[normalized] = snapshot_id
            candidate.searched = True
            for text, source in serp_discoveries(candidate.keyword, data.features):
                added = pool.add(text, source, candidate.keyword)
                if added is not None and added.normalized not in snaps:
                    queue.append(added.normalized)

        if per_search is not None:
            usage.estimated_cost = round(per_search * usage.serpapi_searches, 4)

        brand = brand_tokens_for(run.host)
        candidates = pool.all()
        clusters = cluster_keywords(snaps)
        for cand in candidates:
            snap = snaps.get(cand.normalized)
            cand.intent, cand.intent_basis = classify(
                cand.keyword,
                brand_tokens=brand,
                features=snap.data.features if snap else None,
            )
            cand.cluster = clusters.get(cand.normalized)
        _, _, site_text = await self._site_text(run.host)
        gaps, gap_status = detect_gaps(snaps, run.host, site_text)
        competitors = find_competitors(snaps, run.host)[:MAX_COMPETITORS_STORED]
        opportunities = build_opportunities(candidates, snaps, gaps, run.host)

        actions: dict[str, int] = {}
        for opp in opportunities:
            actions[opp.action] = actions.get(opp.action, 0) + 1
        status = (
            "partially_completed"
            if (
                usage.stopped_reason
                and (unsearched_priority or usage.stopped_reason != "search_budget")
            )
            else "completed"
        )
        summary = {
            "candidates": len(candidates),
            "searched": len(snaps),
            "unsearched_candidates": len(candidates) - len(snaps),
            "clusters": len({c.cluster for c in candidates if c.cluster}),
            "opportunities": len(opportunities),
            "opportunities_by_action": actions,
            "competitors": len(competitors),
            "gaps": len(gaps),
            "ai_overview_present": sum(
                1
                for s in snaps.values()
                if s.data.features.ai_overview.status == "present"
            ),
            "metrics_note": "Search volume, difficulty and CPC are not available from SerpApi.",
        }
        return {
            "status": status,
            "candidates": candidates,
            "snapshot_ids": snapshot_ids,
            "opportunities": opportunities,
            "usage": as_dict(usage),
            "summary": summary,
            "competitors": [as_dict(c) for c in competitors],
            "gaps": [as_dict(g) for g in gaps],
            "gap_status": gap_status,
        }
