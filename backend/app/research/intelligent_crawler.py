"""Intelligent crawler: choose the most relevant mapped pages and scrape them.

Strategy (never recursive, never provider-side link following):

1. select pages from the *mapped* list by relevance, guaranteeing coverage of
   the most informative page types, up to the page budget;
2. re-validate every URL with ``UrlPolicy`` and (optionally) robots.txt;
3. scrape via Firecrawl's batch API;
4. if the batch fails for a transient reason, fall back to individual scraping
   for a limited number of pages (permanent errors such as bad credentials do
   not fall back);
5. retry transiently failed pages once.

Firecrawl's ``/v1/crawl`` is intentionally not used: it follows links on the
provider's side, so we could not revalidate each URL before it is fetched.
"Crawl depth" therefore limits path depth during mapping. Every decision is
written to an audit trail on the result.
"""

import logging
from typing import Protocol

from app.core.config import Settings, get_settings
from app.core.exceptions import ConfigurationException
from app.core.logging import log_event
from app.core.metrics import count, timed
from app.research.robots import RobotsChecker
from app.research.source_manager import SourceManager
from app.research.url_policy import UrlRejected
from app.schemas.intelligence import (
    CrawlAuditEntry,
    CrawlResult,
    CrawlStatus,
    MappedPage,
    PageCrawlResult,
    PageType,
)
from app.schemas.research import ExtractedPage, ExtractionStatus, utc_now
from app.tools.errors import (
    ProviderAuthenticationError,
    ProviderError,
)

logger = logging.getLogger(__name__)

FALLBACK_PAGE_LIMIT = 10
RETRYABLE_CATEGORIES = frozenset(
    {
        "provider_unavailable",
        "provider_timeout",
        "provider_rate_limited",
        "missing_from_batch",
    }
)
#: Types fetched first so a small budget still covers the essentials.
_PRIORITY = (
    PageType.HOMEPAGE,
    PageType.PRICING,
    PageType.PRODUCT,
    PageType.FEATURES,
    PageType.SERVICE,
    PageType.ABOUT,
    PageType.DOCUMENTATION,
)


class CrawlProvider(Protocol):
    """Scrape adapter contract (``FirecrawlTool``)."""

    async def batch_scrape(self, urls: list[str]) -> list[ExtractedPage]: ...

    async def scrape_many(
        self, urls: list[str], *, concurrency: int | None = None
    ) -> list[ExtractedPage]: ...


def select_pages(
    pages: list[MappedPage],
    max_pages: int,
    page_types: list[PageType] | None = None,
) -> list[MappedPage]:
    """Pick up to ``max_pages`` eligible pages, covering priority types first."""
    eligible = [
        p
        for p in pages
        if p.crawl_eligible and (not page_types or p.page_type in page_types)
    ]
    eligible.sort(key=lambda p: -p.relevance_score)
    chosen: list[MappedPage] = []
    picked: set[str] = set()
    for page_type in _PRIORITY:
        best = next((p for p in eligible if p.page_type == page_type), None)
        if best is not None and len(chosen) < max_pages:
            chosen.append(best)
            picked.add(best.normalized_url)
    for page in eligible:
        if len(chosen) >= max_pages:
            break
        if page.normalized_url not in picked:
            chosen.append(page)
            picked.add(page.normalized_url)
    return chosen


class IntelligentCrawler:
    """Bounded, audited page crawling over a mapped website."""

    def __init__(
        self,
        provider: CrawlProvider,
        sources: SourceManager,
        settings: Settings | None = None,
        robots: RobotsChecker | None = None,
    ) -> None:
        self._provider = provider
        self._sources = sources
        self._settings = settings or get_settings()
        self._robots = robots

    async def crawl(
        self,
        website: str,
        pages: list[MappedPage],
        *,
        max_pages: int | None = None,
        page_types: list[PageType] | None = None,
        allowed_domains: list[str] | None = None,
        seen_urls: set[str] | None = None,
    ) -> CrawlResult:
        """Crawl selected pages of ``website``; always returns a result."""
        cfg = self._settings
        budget = min(
            max_pages or cfg.website_research_max_pages_per_site,
            cfg.website_research_max_pages_per_site,
        )
        seen = seen_urls if seen_urls is not None else set()
        result = CrawlResult(website=website, started_at=utc_now())
        selected = select_pages(pages, budget, page_types)
        result.requested_pages = len(selected)
        for page in pages:
            if page not in selected and not page.crawl_eligible:
                self._audit(
                    result, page.normalized_url, "skipped", page.exclusion_reason
                )
        urls = await self._approved_urls(result, selected, allowed_domains, seen)
        if urls:
            await self._scrape(result, urls)
        self._finish(result)
        log_event(
            logger,
            "website_crawled",
            domain=website,
            requested=result.requested_pages,
            ok=result.successful_pages,
            failed=result.failed_pages,
            skipped=result.skipped_pages,
            status=result.crawl_status.value,
        )
        return result

    # -- steps ---------------------------------------------------------------

    async def _approved_urls(
        self,
        result: CrawlResult,
        selected: list[MappedPage],
        allowed_domains: list[str] | None,
        seen: set[str],
    ) -> list[str]:
        with timed("url_validation_and_robots"):
            urls = await self._validate_selected(
                result, selected, allowed_domains, seen
            )
        count("urls_selected", len(urls))
        return urls

    async def _validate_selected(
        self,
        result: CrawlResult,
        selected: list[MappedPage],
        allowed_domains: list[str] | None,
        seen: set[str],
    ) -> list[str]:
        urls: list[str] = []
        for page in selected:
            url = page.normalized_url
            if url in seen:
                self._skip(result, url, "already_crawled")
                continue
            try:
                validated = await self._sources.policy.validate(url, allowed_domains)
            except UrlRejected as exc:
                self._skip(result, url, f"url_rejected:{exc.reason}")
                continue
            if self._robots is not None and not await self._robots.allowed(
                validated.normalized
            ):
                self._skip(
                    result, url, "blocked_by_robots_txt", action="robots_blocked"
                )
                continue
            seen.add(url)
            urls.append(validated.normalized)
            self._audit(result, url, "selected", f"type={page.page_type.value}")
        return urls

    async def _scrape(self, result: CrawlResult, urls: list[str]) -> None:
        method = "batch_scrape"
        try:
            with timed("scrape"):
                pages = await self._provider.batch_scrape(urls)
        except (ProviderAuthenticationError, ConfigurationException) as exc:
            result.errors.append(f"batch scrape failed permanently: {exc}")
            pages = [
                self._failed(u, "provider_authentication_error", str(exc)) for u in urls
            ]
        except ProviderError as exc:
            result.errors.append(f"batch scrape failed ({exc.code}); falling back")
            self._audit(
                result,
                ",".join(urls[:1]),
                "retried",
                "batch failed; individual fallback",
            )
            method = "scrape"
            head, tail = urls[:FALLBACK_PAGE_LIMIT], urls[FALLBACK_PAGE_LIMIT:]
            pages = await self._provider.scrape_many(
                head, concurrency=self._settings.website_research_max_concurrent_pages
            )
            pages += [
                self._failed(u, "fallback_limit", "not fetched: fallback page limit")
                for u in tail
            ]
        outcomes = {
            u: self._outcome(u, p, method, 1) for u, p in zip(urls, pages, strict=True)
        }
        await self._retry(result, outcomes)
        for outcome in outcomes.values():
            result.page_results.append(outcome)
            self._audit(
                result,
                outcome.normalized_url,
                "scraped" if outcome.status == "succeeded" else "failed",
                outcome.error,
            )

    async def _retry(
        self, result: CrawlResult, outcomes: dict[str, PageCrawlResult]
    ) -> None:
        """One bounded retry round for transiently failed pages."""
        again = [
            u
            for u, o in outcomes.items()
            if o.status == "failed"
            and o.page is not None
            and o.page.metadata.get("error_category") in RETRYABLE_CATEGORIES
        ]
        if not again:
            return
        pages = await self._provider.scrape_many(
            again, concurrency=self._settings.website_research_max_concurrent_pages
        )
        for url, page in zip(again, pages, strict=True):
            self._audit(result, url, "retried", "second attempt")
            outcomes[url] = self._outcome(
                url, page, "scrape", outcomes[url].attempts + 1
            )

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _outcome(
        url: str, page: ExtractedPage, method: str, attempts: int
    ) -> PageCrawlResult:
        ok = page.status == ExtractionStatus.SUCCEEDED
        return PageCrawlResult(
            url=url,
            normalized_url=url,
            status="succeeded" if ok else "failed",
            page=page,
            error=None if ok else (page.error_message or "extraction failed"),
            attempts=attempts,
            method=method,
        )

    @staticmethod
    def _failed(url: str, category: str, message: str) -> ExtractedPage:
        return ExtractedPage(
            original_url=url,
            status=ExtractionStatus.FAILED,
            error_message=message[:300],
            metadata={"error_category": category},
        )

    def _skip(
        self, result: CrawlResult, url: str, reason: str, action: str = "skipped"
    ) -> None:
        result.skipped_pages += 1
        result.page_results.append(
            PageCrawlResult(url=url, normalized_url=url, status="skipped", error=reason)
        )
        self._audit(result, url, action, reason)

    @staticmethod
    def _audit(result: CrawlResult, url: str, action: str, detail: str | None) -> None:
        result.audit.append(CrawlAuditEntry(url=url, action=action, detail=detail))

    @staticmethod
    def _finish(result: CrawlResult) -> None:
        ran = [r for r in result.page_results if r.status != "skipped"]
        result.processed_pages = len(ran)
        result.successful_pages = sum(r.status == "succeeded" for r in ran)
        result.failed_pages = sum(r.status == "failed" for r in ran)
        if not ran:
            result.crawl_status = CrawlStatus.EMPTY
        elif result.successful_pages == 0:
            result.crawl_status = CrawlStatus.FAILED
        elif result.failed_pages:
            result.crawl_status = CrawlStatus.PARTIAL
        else:
            result.crawl_status = CrawlStatus.COMPLETED
        result.completed_at = utc_now()
