"""Technical SEO audit service: plan -> approve and run -> stored results.

- ``plan`` validates the request and stores a ``planned`` audit with an
  upper-bound estimate. It makes **no network request**.
- ``run`` requires an explicit approval with a Firecrawl scrape ceiling no higher
  than the estimate, refuses a second concurrent audit of the same site, then
  collects (bounded by the approved budget and ``SEO_AUDIT_MAX_DURATION_SECONDS``),
  applies the rules, builds recommendations and stores everything in one
  transaction. Runs are synchronous (the request waits); a background runner is
  a later phase.
"""

import logging
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.core.config import Settings
from app.core.exceptions import (
    ConfigurationException,
    ConflictException,
    ResearchException,
    ResourceNotFoundException,
)
from app.database.models import SeoAuditRecord
from app.database.repositories.seo_audit_repository import SeoAuditRepository
from app.research.robots import USER_AGENT
from app.research.url_policy import UrlPolicy, UrlRejected
from app.schemas.research import utc_now
from app.seo.technical.collector import (
    CollectionResult,
    PageScraper,
    RequestBudget,
    SiteCollector,
)
from app.seo.technical.html_signals import site_host
from app.seo.technical.models import AuditEstimate, AuditLimits
from app.seo.technical.recommendations import build_recommendations
from app.seo.technical.rules import run_rules

logger = logging.getLogger(__name__)

FILE_REQUESTS = 5  # one site file: the request plus up to four redirects


def estimate_for(limits: AuditLimits, max_redirects: int) -> AuditEstimate:
    per_url = max_redirects + 2  # every hop, plus one GET fallback
    direct = FILE_REQUESTS * (1 + limits.max_sitemaps) + per_url * (
        limits.max_pages + limits.max_link_checks
    )
    return AuditEstimate(
        firecrawl_scrapes_max=limits.max_pages,
        direct_requests_max=direct,
        notes=[
            "Firecrawl: at most one scrape (raw HTML + links) per audited page; only pages whose HTTP "
            "trace ends in an HTML 2xx response are scraped. Credits per scrape depend on your plan.",
            "Direct requests go from this machine to the audited site only (robots.txt, sitemaps, HEAD "
            "traces, link checks); they are not billed by a provider.",
            "SerpApi and LLM providers are not used by the technical audit.",
        ],
    )


class TechnicalSeoService:
    def __init__(
        self,
        *,
        repository: SeoAuditRepository,
        settings: Settings,
        policy: UrlPolicy,
        scraper: PageScraper,
        client_factory: Callable[[RequestBudget], httpx.AsyncClient] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repo = repository
        self._settings = settings
        self._policy = policy
        self._scraper = scraper
        self._client_factory = client_factory or self._default_client
        self._clock = clock

    def _default_client(self, budget: RequestBudget) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._settings.seo_audit_http_timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
            event_hooks={"request": [budget.on_request]},
        )

    # -- plan -----------------------------------------------------------------------------

    def _limits(
        self,
        max_pages: int | None,
        max_link_checks: int | None,
        max_sitemaps: int | None,
    ) -> AuditLimits:
        s = self._settings

        def bounded(value: int | None, default: int, name: str) -> int:
            if value is None:
                return default
            if value > default:
                raise ResearchException(
                    f"{name} may not exceed the configured maximum of {default}."
                )
            return value

        return AuditLimits(
            max_pages=bounded(max_pages, s.seo_audit_max_pages, "max_pages"),
            max_link_checks=bounded(
                max_link_checks, s.seo_audit_max_link_checks, "max_link_checks"
            ),
            max_sitemaps=bounded(
                max_sitemaps, s.seo_audit_max_sitemaps, "max_sitemaps"
            ),
            max_duration_seconds=s.seo_audit_max_duration_seconds,
        )

    async def plan(
        self,
        url: str,
        *,
        max_pages: int | None = None,
        max_link_checks: int | None = None,
        max_sitemaps: int | None = None,
    ) -> SeoAuditRecord:
        try:
            normalized, host = self._policy.normalize(url)
        except UrlRejected as exc:
            raise ResearchException(f"The URL is not allowed: {exc.message}") from exc
        limits = self._limits(max_pages, max_link_checks, max_sitemaps)
        estimate = estimate_for(limits, self._settings.research_max_redirects)
        record = SeoAuditRecord(
            id=uuid4(),
            site_url=normalized,
            host=site_host(host),
            status="planned",
            limits=limits.model_dump(),
            estimate=estimate.model_dump(),
            approval=None,
            usage={},
            coverage={},
            site_files={},
            summary={},
            created_at=utc_now(),
        )
        return await self._repo.create(record)

    # -- run ------------------------------------------------------------------------------

    async def run(
        self, audit_id: UUID, *, max_firecrawl_scrapes: int
    ) -> SeoAuditRecord:
        audit = await self.get(audit_id)
        if audit.status != "planned":
            raise ConflictException(
                f"Audit is '{audit.status}'; only a planned audit can be run."
            )
        estimate = AuditEstimate.model_validate(audit.estimate)
        if max_firecrawl_scrapes > estimate.firecrawl_scrapes_max:
            raise ResearchException(
                f"max_firecrawl_scrapes ({max_firecrawl_scrapes}) exceeds the plan's maximum "
                f"({estimate.firecrawl_scrapes_max}); create a new plan for a larger audit."
            )
        ensure = getattr(self._scraper, "ensure_configured", None)
        if callable(ensure):
            ensure()  # ConfigurationException (503) before anything is marked or fetched
        if await self._repo.running_for_host(audit.host) is not None:
            raise ConflictException("Another audit of this site is already running.")
        approval = {
            "approved_at": utc_now().isoformat(),
            "max_firecrawl_scrapes": max_firecrawl_scrapes,
            "max_direct_requests": estimate.direct_requests_max,
        }
        if not await self._repo.mark_running(audit_id, approval):
            raise ConflictException("The audit was started by another request.")

        limits = AuditLimits.model_validate(audit.limits)
        budget = RequestBudget(
            max_direct_requests=estimate.direct_requests_max,
            max_scrapes=max_firecrawl_scrapes,
            deadline=self._clock() + limits.max_duration_seconds,
            clock=self._clock,
        )
        try:
            async with self._client_factory(budget) as client:
                collector = SiteCollector(
                    policy=self._policy,
                    scraper=self._scraper,
                    client=client,
                    budget=budget,
                    limits=limits,
                    max_html_chars=self._settings.seo_audit_max_html_chars,
                    max_sitemap_bytes=self._settings.seo_audit_max_sitemap_bytes,
                    max_sitemap_urls=self._settings.seo_audit_max_sitemap_urls,
                    concurrency=self._settings.seo_audit_concurrency,
                )
                result = await collector.collect(audit.site_url)
        except UrlRejected as exc:
            await self._repo.mark_failed(
                audit_id,
                f"The site URL was rejected: {exc.message}",
                budget.usage.model_dump(),
            )
            return await self.get(audit_id, refresh=True)
        except ConfigurationException:
            await self._repo.mark_failed(
                audit_id, "A provider is not configured.", budget.usage.model_dump()
            )
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, never leaked
            logger.error(
                "seo_audit_failed audit_id=%s error=%s", audit_id, type(exc).__name__
            )
            await self._repo.mark_failed(
                audit_id, "The audit failed unexpectedly.", budget.usage.model_dump()
            )
            return await self.get(audit_id, refresh=True)

        findings = run_rules(result)
        audited = sum(
            1
            for p in result.pages
            if p.signals is not None or p.status_code is not None
        )
        recommendations = build_recommendations(
            findings, start_url=result.start_url, total_pages=max(1, audited)
        )
        status = (
            "partially_completed" if result.coverage.stopped_reason else "completed"
        )
        await self._repo.save_results(
            audit_id,
            status=status,
            pages=result.pages,
            findings=findings,
            recommendations=recommendations,
            usage=result.usage.model_dump(),
            coverage=result.coverage.model_dump(),
            site_files=_site_files(result),
            summary=_summary(result, findings, recommendations),
        )
        return await self.get(audit_id, refresh=True)

    # -- reads ----------------------------------------------------------------------------

    async def get(self, audit_id: UUID, *, refresh: bool = False) -> SeoAuditRecord:
        audit = await self._repo.get(audit_id)
        if audit is None:
            raise ResourceNotFoundException("Audit not found.")
        if refresh:
            await self._repo.refresh(audit)
        return audit

    @property
    def repository(self) -> SeoAuditRepository:
        return self._repo


def _site_files(result: CollectionResult) -> dict[str, Any]:
    robots = result.robots
    return {
        "robots": None
        if robots is None
        else {
            "url": robots.file.url,
            "outcome": robots.file.outcome,
            "status": robots.file.status,
            "applies": robots.applies,
            "sitemaps": robots.sitemaps,
            "googlebot_disallows_root": robots.googlebot_disallows_root,
            "has_googlebot_group": robots.has_googlebot_group,
            "invalid_lines": robots.invalid_lines,
            "size": robots.file.size,
        },
        "sitemaps": [
            {
                "url": s.url,
                "kind": s.kind,
                "outcome": s.file.outcome,
                "status": s.file.status,
                "url_count": s.url_count,
                "children": len(s.children),
                "lastmod_count": s.lastmod_count,
                "error": s.error,
                "truncated": s.truncated,
            }
            for s in result.sitemaps
        ],
    }


def _summary(
    result: CollectionResult, findings: list[Any], recommendations: list[Any]
) -> dict[str, Any]:
    severities: dict[str, int] = {}
    for finding in findings:
        severities[finding.severity] = severities.get(finding.severity, 0) + 1
    indexability: dict[str, int] = {}
    for page in result.pages:
        indexability[page.indexability.state] = (
            indexability.get(page.indexability.state, 0) + 1
        )
    tiers: dict[str, int] = {}
    for rec in recommendations:
        tiers[rec.tier] = tiers.get(rec.tier, 0) + 1
    return {
        "findings_by_severity": severities,
        "pages_by_indexability": indexability,
        "recommendations_by_tier": tiers,
        "findings": len(findings),
        "recommendations": len(recommendations),
    }
