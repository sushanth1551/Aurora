"""Bounded collection of technical SEO evidence for one site.

Order: robots.txt -> sitemaps -> pages (breadth-first from the start URL, plus
sitemap URLs) -> internal link checks. For every page:

1. robots.txt is checked for AURORA's own user agent (courtesy; disallowed pages
   are recorded, never requested). Googlebot's verdict is recorded separately.
2. ``UrlPolicy.trace_http`` follows redirects manually (HEAD, GET fallback),
   revalidating every hop and staying on the audited site; it yields statuses
   and allow-listed headers.
3. Only an HTML 2xx final URL is fetched as raw HTML through
   ``FirecrawlTool.scrape_for_seo`` (one credit), within the approved budget.

Budgets: ``RequestBudget`` counts every direct HTTP request (an httpx request
hook) and every Firecrawl scrape; reaching a ceiling or the deadline stops the
collection, and the result says why (``coverage.stopped_reason``).
"""

import asyncio
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from app.research.robots import USER_AGENT
from app.research.url_policy import HttpTrace, UrlPolicy, UrlRejected
from app.research.website_mapper import exclusion_reason, strip_tracking
from app.schemas.research import utc_now
from app.seo.technical.html_signals import (
    has_noindex,
    is_internal,
    parse_html,
    robots_directives,
    site_host,
)
from app.seo.technical.models import (
    AuditCoverage,
    AuditLimits,
    AuditUsage,
    HopView,
    Indexability,
    LinkCheck,
    PageResult,
)
from app.seo.technical.site_files import (
    RobotsReport,
    SitemapReport,
    analyze_robots,
    fetch_site_file,
    parse_sitemap,
)
from app.seo.technical.structured_data import analyze_json_ld
from app.tools.errors import ProviderError
from app.tools.firecrawl_tool import SeoScrape

HTML_TYPES = ("text/html", "application/xhtml+xml")


class BudgetExhausted(Exception):
    """A request or scrape ceiling, or the deadline, was reached."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class PageScraper(Protocol):
    async def scrape_for_seo(self, url: str, *, max_html_chars: int) -> SeoScrape: ...


@dataclass
class RequestBudget:
    """Ceilings approved for one audit; ``usage`` is what was actually spent."""

    max_direct_requests: int
    max_scrapes: int
    deadline: float
    clock: Callable[[], float] = time.monotonic
    usage: AuditUsage = field(default_factory=AuditUsage)

    def check_deadline(self) -> None:
        if self.clock() >= self.deadline:
            raise BudgetExhausted("deadline")

    def count_request(self) -> None:
        self.check_deadline()
        if self.usage.direct_requests >= self.max_direct_requests:
            raise BudgetExhausted("direct_request_budget")
        self.usage.direct_requests += 1

    def reserve_scrape(self) -> None:
        self.check_deadline()
        if self.usage.firecrawl_scrapes >= self.max_scrapes:
            raise BudgetExhausted("firecrawl_scrape_budget")
        self.usage.firecrawl_scrapes += 1

    async def on_request(self, _: httpx.Request) -> None:
        """httpx event hook: every outgoing direct request is counted first."""
        self.count_request()


@dataclass
class CollectionResult:
    site: str
    start_url: str
    pages: list[PageResult]
    robots: RobotsReport | None
    sitemaps: list[SitemapReport]
    link_checks: list[LinkCheck]
    usage: AuditUsage
    coverage: AuditCoverage


def evaluate_indexability(page: PageResult) -> Indexability:
    """Signals that make a page ineligible; never claims a page is indexed."""
    if (
        page.fetch_status in ("pending", "skipped_robots", "budget_exhausted")
        and page.status_code is None
    ):
        if page.googlebot_allowed is False:
            return Indexability(
                state="not_indexable", reasons=["robots_txt_disallows_googlebot"]
            )
        return Indexability(state="unknown", reasons=["not_fetched"])
    reasons = []
    if page.googlebot_allowed is False:
        reasons.append("robots_txt_disallows_googlebot")
    if page.redirect_chain and len(page.redirect_chain) > 1:
        reasons.append("redirects")
    if page.status_code is None:
        reasons.append("status_unknown")
    elif not 200 <= page.status_code < 300:
        reasons.append(f"http_{page.status_code}")
    if page.signals is not None:
        directives = robots_directives(page.signals, _header_list(page, "x-robots-tag"))
        if has_noindex(directives):
            reasons.append("noindex")
        target = page.final_url or page.url
        canonicals = set(page.signals.canonicals)
        if len(canonicals) == 1 and _strip_slash(
            next(iter(canonicals))
        ) != _strip_slash(target):
            reasons.append("canonical_points_elsewhere")
    elif page.fetch_source != "firecrawl_raw_html" and not reasons:
        return Indexability(state="unknown", reasons=["html_not_fetched"])
    if reasons:
        return Indexability(state="not_indexable", reasons=reasons)
    return Indexability(state="indexable_candidate")


def _strip_slash(url: str) -> str:
    return url[:-1] if url.endswith("/") and urlsplit(url).path != "/" else url


def _header_list(page: PageResult, name: str) -> list[str]:
    value = page.headers.get(name)
    return [value] if value else []


class SiteCollector:
    def __init__(
        self,
        *,
        policy: UrlPolicy,
        scraper: PageScraper,
        client: httpx.AsyncClient,
        budget: RequestBudget,
        limits: AuditLimits,
        max_html_chars: int,
        max_sitemap_bytes: int,
        max_sitemap_urls: int,
        concurrency: int,
    ) -> None:
        self._policy = policy
        self._scraper = scraper
        self._client = client
        self._budget = budget
        self._limits = limits
        self._max_html = max_html_chars
        self._max_sitemap_bytes = max_sitemap_bytes
        self._max_sitemap_urls = max_sitemap_urls
        self._gate = asyncio.Semaphore(max(1, concurrency))

    async def collect(self, start_url: str) -> CollectionResult:
        start = (await self._policy.validate(start_url)).normalized
        parts = urlsplit(start)
        site = site_host(parts.hostname or "")
        origin = f"{parts.scheme}://{parts.netloc}"
        coverage = AuditCoverage()
        robots: RobotsReport | None = None
        sitemaps: list[SitemapReport] = []
        pages: dict[str, PageResult] = {}
        links: list[LinkCheck] = []
        try:
            robots = await self._robots(origin, site)
            sitemaps = await self._sitemaps(origin, site, robots)
            coverage.sitemap_urls_found = sum(
                s.url_count for s in sitemaps if s.kind == "urlset"
            )
            await self._crawl(start, site, robots, sitemaps, pages)
            links = await self._check_links(site, robots, pages, coverage)
        except BudgetExhausted as exc:
            coverage.stopped_reason = exc.reason
        for page in pages.values():
            page.indexability = evaluate_indexability(page)
        fetched = [p for p in pages.values() if p.fetch_status not in ("pending",)]
        coverage.pages_audited = sum(
            1 for p in fetched if p.status_code is not None or p.signals
        )
        coverage.pages_discovered = len(pages)
        coverage.pages_not_fetched = len(pages) - coverage.pages_audited
        return CollectionResult(
            site=site,
            start_url=start,
            pages=list(pages.values()),
            robots=robots,
            sitemaps=sitemaps,
            link_checks=links,
            usage=self._budget.usage,
            coverage=coverage,
        )

    # -- site files -------------------------------------------------------------------

    async def _robots(self, origin: str, site: str) -> RobotsReport:
        file = await fetch_site_file(
            f"{origin}/robots.txt",
            policy=self._policy,
            client=self._client,
            allowed_domains=[site],
            max_bytes=512 * 1024,
        )
        return analyze_robots(file)

    async def _sitemaps(
        self, origin: str, site: str, robots: RobotsReport
    ) -> list[SitemapReport]:
        queue = [u for u in robots.sitemaps if is_internal(u, site)] or [
            f"{origin}/sitemap.xml"
        ]
        seen: set[str] = set()
        reports: list[SitemapReport] = []
        while queue and len(reports) < self._limits.max_sitemaps:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            file = await fetch_site_file(
                url,
                policy=self._policy,
                client=self._client,
                allowed_domains=[site],
                max_bytes=self._max_sitemap_bytes,
            )
            report = parse_sitemap(file, self._max_sitemap_urls)
            reports.append(report)
            queue.extend(
                c for c in report.children if is_internal(c, site) and c not in seen
            )
        return reports

    # -- pages ----------------------------------------------------------------------------

    def _candidate(self, url: str, site: str) -> str | None:
        try:
            normalized, _ = self._policy.normalize(strip_tracking(url), [site])
        except UrlRejected:
            return None
        if (
            not is_internal(normalized, site)
            or exclusion_reason(normalized) == "non_html_asset"
        ):
            return None
        return normalized

    async def _crawl(
        self,
        start: str,
        site: str,
        robots: RobotsReport,
        sitemaps: list[SitemapReport],
        pages: dict[str, PageResult],
    ) -> None:
        sitemap_urls = [
            c for s in sitemaps for u in s.urls if (c := self._candidate(u, site))
        ]
        wave: list[tuple[str, int, str]] = [(start, 0, "start")]
        sitemap_added = False
        while wave and len(pages) < self._limits.max_pages:
            room = self._limits.max_pages - len(pages)
            batch: list[PageResult] = []
            for url, depth, via in wave:
                if url in pages:
                    if via not in pages[url].discovered_via:
                        pages[url].discovered_via.append(via)
                    continue
                if len(batch) >= room:
                    break
                pages[url] = PageResult(url=url, depth=depth, discovered_via=[via])
                batch.append(pages[url])
            # return_exceptions: let every in-flight page finish before stopping.
            outcomes = await asyncio.gather(
                *(self._audit_page(p, site, robots) for p in batch),
                return_exceptions=True,
            )
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise outcome
            next_wave: list[tuple[str, int, str]] = []
            for page in batch:
                for link in page.signals.links if page.signals else []:
                    if (
                        link.internal
                        and not link.nofollow
                        and (c := self._candidate(link.href, site))
                    ):
                        next_wave.append((c, page.depth + 1, "link"))
            if not sitemap_added:
                next_wave.extend((u, 1, "sitemap") for u in sitemap_urls)
                sitemap_added = True
            wave = list(dict.fromkeys(next_wave))
        for url in sitemap_urls:  # record sitemap membership of audited pages
            if url in pages and "sitemap" not in pages[url].discovered_via:
                pages[url].discovered_via.append("sitemap")

    async def _audit_page(
        self, page: PageResult, site: str, robots: RobotsReport
    ) -> None:
        async with self._gate:
            page.googlebot_allowed = robots.allows("Googlebot", page.url)
            if robots.allows(USER_AGENT, page.url) is False:
                page.fetch_status = "skipped_robots"
                page.fetch_detail = (
                    "robots.txt disallows AURORA's crawler; not requested"
                )
                return
            trace: HttpTrace | None = None
            try:
                trace = await self._policy.trace_http(page.url, self._client, [site])
            except UrlRejected as exc:
                page.fetch_detail = exc.reason
                if exc.reason in ("redirect_blocked", "too_many_redirects"):
                    page.fetch_status = (
                        "redirect_offsite"
                        if exc.reason == "redirect_blocked"
                        else "redirect_loop"
                    )
                    return
                page.fetch_status = "trace_failed"
            if trace is not None:
                page.redirect_chain = [
                    HopView(url=h.url, status=h.status, method=h.method)
                    for h in trace.hops
                ]
                page.status_code = trace.final_status
                page.status_source = "http_trace"
                page.final_url = trace.final_url
                page.headers = dict(trace.final_headers)
                page.fetched_at = utc_now()
                if not is_internal(trace.final_url, site):
                    page.fetch_status = "redirect_offsite"
                    return
                content_type = page.headers.get("content-type", "").lower()
                if not 200 <= trace.final_status < 300:
                    page.fetch_source, page.fetch_status = (
                        "http_only",
                        "http_error" if trace.final_status >= 400 else "no_html",
                    )
                    return
                if content_type and not content_type.startswith(HTML_TYPES):
                    page.fetch_source, page.fetch_status = "http_only", "not_html"
                    return
            await self._scrape(page)

    async def _scrape(self, page: PageResult) -> None:
        target = page.final_url or page.url
        try:
            self._budget.reserve_scrape()
        except BudgetExhausted:
            page.fetch_status = "budget_exhausted"
            raise
        try:
            scrape = await self._scraper.scrape_for_seo(
                target, max_html_chars=self._max_html
            )
        except ProviderError as exc:
            self._budget.usage.firecrawl_failures += 1
            page.fetch_status, page.fetch_detail = "scrape_failed", exc.code
            return
        page.firecrawl_status = scrape.status_code
        if (
            page.status_code is None
        ):  # trace failed: only the provider's word is available
            page.status_code, page.status_source = (
                scrape.status_code,
                "firecrawl_reported",
            )
            page.final_url = page.final_url or target
        page.fetched_at = scrape.fetched_at
        if not scrape.raw_html.strip():
            page.fetch_status, page.fetch_detail = "scrape_failed", "empty_html"
            return
        page.html_sha256 = hashlib.sha256(
            scrape.raw_html.encode("utf-8", errors="replace")
        ).hexdigest()
        page.html_truncated = scrape.truncated
        page.signals = parse_html(
            scrape.raw_html, target, site_host(urlsplit(target).hostname or "")
        )
        page.structured_data = analyze_json_ld(
            page.signals.json_ld_blocks,
            microdata=page.signals.microdata_present,
            rdfa=page.signals.rdfa_present,
        )
        page.fetch_source, page.fetch_status = "firecrawl_raw_html", "ok"

    # -- internal link checks -------------------------------------------------------

    async def _check_links(
        self,
        site: str,
        robots: RobotsReport,
        pages: dict[str, PageResult],
        coverage: AuditCoverage,
    ) -> list[LinkCheck]:
        targets: dict[str, LinkCheck] = {}
        for page in pages.values():
            for link in page.signals.links if page.signals else []:
                if not link.internal:
                    continue
                url = self._candidate(link.href, site)
                if url is None:
                    continue
                check = targets.setdefault(url, LinkCheck(url=url))
                if page.url not in check.referrers and len(check.referrers) < 50:
                    check.referrers.append(page.url)
                if (
                    link.text
                    and len(check.anchors) < 5
                    and link.text not in check.anchors
                ):
                    check.anchors.append(link.text)
        coverage.link_targets_found = len(targets)
        unknown = []
        for url, check in targets.items():
            known = pages.get(url)
            if known is not None and known.status_code is not None:
                _classify(
                    check,
                    known.status_code,
                    known.final_url,
                    len(known.redirect_chain) - 1,
                )
            else:
                unknown.append(check)
        unknown.sort(key=lambda c: -len(c.referrers))
        checked = 0
        for check in unknown[: self._limits.max_link_checks]:
            if robots.allows(USER_AGENT, check.url) is False:
                check.outcome = "skipped_robots"
                continue
            try:
                trace = await self._policy.trace_http(check.url, self._client, [site])
            except UrlRejected:
                check.outcome = "unverified"
                continue
            finally:
                checked += 1
                coverage.link_targets_checked = checked
            _classify(check, trace.final_status, trace.final_url, trace.redirect_count)
        return list(targets.values())


def _classify(
    check: LinkCheck, status: int, final_url: str | None, redirects: int
) -> None:
    check.status, check.final_url, check.redirect_count = (
        status,
        final_url,
        max(0, redirects),
    )
    if status >= 400:
        check.outcome = "broken"
    elif redirects > 0:
        check.outcome = "redirect"
    else:
        check.outcome = "ok"
