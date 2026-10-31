"""Firecrawl adapter: scrape single pages to Markdown.

Uses HTTPX against the documented ``POST {api}/v1/scrape`` endpoint (the same
one ``firecrawl-py`` 1.x calls) rather than the SDK, because the SDK is
synchronous (``requests``) and does not expose ``Retry-After`` or response-size
control. Request body: ``{"url", "formats": ["markdown"], "onlyMainContent",
"timeout"}``; success response: ``{"success": true, "data": {"markdown",
"metadata": {"title", "description", "statusCode", "sourceURL", "url", ...}}}``.

Only single-page scraping is implemented; there is no site crawling. Every URL
is also checked with the synchronous part of ``UrlPolicy`` as a last line of
defence; callers must run the full (DNS-aware) validation first. Fetching
happens on Firecrawl's infrastructure, so redirect and DNS behaviour there is
outside our control (see ``docs/security-and-ssrf.md``).
"""

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from app.core import metrics
from app.core.concurrency import shared_gate
from app.core.config import Settings
from app.core.hashing import content_hash
from app.core.logging import log_event
from app.research.url_policy import UrlPolicy, UrlRejected
from app.schemas.research import ExtractedPage, ExtractionStatus, utc_now
from app.tools.base import ExternalTool
from app.tools.errors import (
    ProviderError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.tools.http import (
    build_timeout,
    client_scope,
    configure_circuit_breaker,
    rate_limit_only,
    retry_async,
    send_bounded,
)

logger = logging.getLogger(__name__)

FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"
FIRECRAWL_MAP_URL = "https://api.firecrawl.dev/v1/map"
FIRECRAWL_BATCH_URL = "https://api.firecrawl.dev/v1/batch/scrape"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
BATCH_CHUNK_SIZE = 25
_OPTIONAL_METADATA = (
    "ogSiteName",
    "ogUrl",
    "canonical",
    "canonicalUrl",
    "publishedTime",
    "modifiedTime",
    "article:published_time",
    "article:modified_time",
)


def _endpoint_label(url: str) -> str:
    """Endpoint path without job ids, e.g. ``/v1/batch/scrape/status``."""
    path = urlsplit(url).path
    if path.startswith("/v1/batch/scrape/"):
        return "/v1/batch/scrape/status"
    return path


def _url_key(url: str) -> str:
    """Comparison key for matching provider results to requested URLs."""
    return url.strip().rstrip("/")


class MapLink(BaseModel):
    """One URL returned by Firecrawl's map endpoint."""

    url: str
    title: str | None = None
    description: str | None = None


def _text(value: Any, limit: int = 2000) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


MAX_SEO_LINKS = 2000


class SeoScrape(BaseModel):
    """Raw HTML of one page as Firecrawl fetched it (for technical SEO parsing).

    ``provider_final_url`` and ``status_code`` are reported by Firecrawl and are
    not verified by AURORA's URL policy; callers must treat them as such.
    Firecrawl renders pages in a browser, so ``raw_html`` may contain
    JavaScript-generated markup and is not a byte-exact copy of the response.
    """

    url: str
    provider_final_url: str | None = None
    status_code: int | None = None
    raw_html: str = ""
    links: list[str] = []
    truncated: bool = False
    original_length: int = 0
    fetched_at: datetime


class FirecrawlTool(ExternalTool):
    """Extract page content through Firecrawl with bounded retries and sizes."""

    provider = "Firecrawl"
    required_settings = ("firecrawl_api_key",)

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        super().__init__(settings)
        self._client = client
        self._sleep = sleep
        self._rng = rng
        self._guard = UrlPolicy()
        configure_circuit_breaker(
            self.provider,
            self._settings.provider_circuit_failure_threshold,
            self._settings.provider_circuit_cooldown_seconds,
        )

    def _slot(self) -> asyncio.Semaphore:
        """Global (all jobs) cap on simultaneous Firecrawl requests; a local limit."""
        return shared_gate(
            "firecrawl", self._settings.firecrawl_max_concurrent_requests
        )

    async def scrape_url(self, url: str) -> ExtractedPage:
        """Scrape one validated public URL.

        Returns an ``ExtractedPage``; content-level problems (empty Markdown,
        target page HTTP errors) come back with ``status=FAILED`` and an
        ``error_message``. Provider-level problems raise ``ProviderError``.
        """
        self.ensure_configured()
        try:
            self._guard.normalize(url)
        except UrlRejected as exc:
            raise ProviderRequestError(
                self.provider, f"URL refused: {exc.reason}"
            ) from exc
        started = time.monotonic()
        payload = await retry_async(
            lambda: self._fetch(url),
            provider=self.provider,
            max_retries=self._settings.research_max_retries,
            base_delay=self._settings.research_retry_base_delay_seconds,
            max_delay=self._settings.research_retry_max_delay_seconds,
            sleep=self._sleep,
            rng=self._rng,
        )
        page = self._to_page(url, payload)
        log_event(
            logger,
            "provider_call",
            provider=self.provider,
            operation="scrape",
            status=page.status.value,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return page

    async def scrape_many(
        self, urls: list[str], *, concurrency: int | None = None
    ) -> list[ExtractedPage]:
        """Scrape ``urls`` with bounded concurrency; results align with input.

        Provider failures become ``FAILED`` pages carrying ``error_category``
        in their metadata, so one bad page never aborts the batch.
        """
        limit = concurrency or self._settings.research_max_concurrent_extractions
        gate = asyncio.Semaphore(max(1, limit))

        async def one(url: str) -> ExtractedPage:
            async with gate:
                try:
                    return await self.scrape_url(url)
                except ProviderError as exc:
                    return self._failed(url, exc.message, exc.code, exc.provider_status)
                except Exception as exc:  # noqa: BLE001 - isolate per-page failures
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    return self._failed(
                        url, "unexpected extraction error", "internal_error"
                    )

        return list(await asyncio.gather(*(one(u) for u in urls)))

    # -- internals ------------------------------------------------------------

    async def _fetch(self, url: str) -> dict[str, Any]:
        key = self._settings.firecrawl_api_key
        assert key is not None  # guaranteed by ensure_configured()
        read = self._settings.firecrawl_scrape_timeout_seconds
        timeout = build_timeout(
            self._settings.research_http_connect_timeout_seconds, read
        )
        body = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": int(read * 900),
        }
        headers = {"Authorization": f"Bearer {key.get_secret_value()}"}
        async with client_scope(self._client, timeout) as client:
            request = client.build_request(
                "POST",
                FIRECRAWL_SCRAPE_URL,
                json=body,
                headers=headers,
                timeout=timeout,
            )
            async with self._slot():
                _, raw = await send_bounded(
                    self.provider, client, request, max_bytes=MAX_RESPONSE_BYTES
                )
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise ProviderResponseError(self.provider, "response was not JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderResponseError(self.provider, "unexpected response shape")
        if payload.get("success") is not True or not isinstance(
            payload.get("data"), dict
        ):
            raise ProviderResponseError(self.provider, "scrape reported failure")
        return payload["data"]

    def _to_page(self, url: str, data: dict[str, Any]) -> ExtractedPage:
        meta = data.get("metadata")
        meta = meta if isinstance(meta, dict) else {}
        status_code = meta.get("statusCode")
        provider_status = status_code if isinstance(status_code, int) else None
        reported_final = _text(meta.get("url"), 2048)
        metadata: dict[str, Any] = {}
        if lang := _text(meta.get("language"), 20):
            metadata["language"] = lang
        # Optional descriptive fields; names follow Firecrawl v1 page metadata
        # and are kept only when present (never invented).
        for key in _OPTIONAL_METADATA:
            if (value := _text(meta.get(key), 500)) is not None:
                metadata[key] = value
        if reported_final and reported_final != url:
            # Unverified: SourceManager promotes it to final_url only if it
            # passes URL policy.
            metadata["provider_final_url"] = reported_final
        page = ExtractedPage(
            original_url=url,
            title=_text(meta.get("title"), 1000),
            description=_text(meta.get("description"), 5000),
            provider_status=provider_status,
            extracted_at=utc_now(),
            metadata=metadata,
        )
        markdown = data.get("markdown")
        markdown = markdown.strip() if isinstance(markdown, str) else ""
        if provider_status is not None and provider_status >= 400:
            return self._mark_failed(
                page,
                f"target page returned HTTP {provider_status}",
                "target_http_error",
            )
        if not markdown:
            return self._mark_failed(
                page, "no Markdown content extracted", "empty_content"
            )
        limit = self._settings.research_max_content_chars
        if len(markdown) > limit:
            page.metadata["truncated"] = True
            page.metadata["original_length"] = len(markdown)
            markdown = markdown[:limit]
        page.markdown = markdown
        page.content_hash = content_hash(markdown)
        page.status = ExtractionStatus.SUCCEEDED
        return page

    # -- raw HTML for technical SEO (Phase 2) ------------------------------------

    async def scrape_for_seo(self, url: str, *, max_html_chars: int) -> SeoScrape:
        """Fetch one validated public URL as raw HTML plus its link list.

        Separate from ``scrape_url`` (which stays Markdown-only): requests the
        ``rawHtml`` and ``links`` formats with ``onlyMainContent: false`` so the
        ``<head>``, navigation and footer are kept. One Firecrawl scrape (credit)
        per call. Provider-level problems raise ``ProviderError``.
        """
        self.ensure_configured()
        try:
            self._guard.normalize(url)
        except UrlRejected as exc:
            raise ProviderRequestError(
                self.provider, f"URL refused: {exc.reason}"
            ) from exc
        read = self._settings.firecrawl_scrape_timeout_seconds
        body = {
            "url": url,
            "formats": ["rawHtml", "links"],
            "onlyMainContent": False,
            "timeout": int(read * 900),
        }
        payload = await self._request_json(
            "POST", FIRECRAWL_SCRAPE_URL, body, read_timeout=read
        )
        if payload.get("success") is not True or not isinstance(
            payload.get("data"), dict
        ):
            raise ProviderResponseError(self.provider, "scrape reported failure")
        data = payload["data"]
        meta = data.get("metadata")
        meta = meta if isinstance(meta, dict) else {}
        status = meta.get("statusCode")
        raw = data.get("rawHtml")
        raw = raw if isinstance(raw, str) else ""
        links = data.get("links")
        kept = [
            link[:2048]
            for link in (links if isinstance(links, list) else [])
            if isinstance(link, str)
        ][:MAX_SEO_LINKS]
        return SeoScrape(
            url=url,
            provider_final_url=_text(meta.get("url"), 2048),
            status_code=status if isinstance(status, int) else None,
            raw_html=raw[:max_html_chars],
            links=kept,
            truncated=len(raw) > max_html_chars,
            original_length=len(raw),
            fetched_at=utc_now(),
        )

    # -- website mapping and batch scraping (Module 3) ---------------------------

    async def _request_json(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
        *,
        read_timeout: float | None = None,
        safe_retries_only: bool = False,
    ) -> dict[str, Any]:
        """One authenticated, size-bounded JSON request with bounded retries.

        ``safe_retries_only`` is for credit-spending, non-idempotent calls (map,
        batch start): only a 429 (rejected before processing) is retried, up to
        ``WEBSITE_RESEARCH_MAX_RETRIES``; a timeout or 5xx is reported, because
        the provider may have done (and billed) the work. Records endpoint,
        duration and failure category, never job ids, headers or bodies.
        """
        self.ensure_configured()
        key = self._settings.firecrawl_api_key
        assert key is not None
        timeout = build_timeout(
            self._settings.research_http_connect_timeout_seconds,
            read_timeout or self._settings.research_http_read_timeout_seconds,
        )
        headers = {"Authorization": f"Bearer {key.get_secret_value()}"}

        async def once() -> dict[str, Any]:
            async with client_scope(self._client, timeout) as client:
                request = client.build_request(
                    method, url, json=body, headers=headers, timeout=timeout
                )
                async with self._slot():
                    _, raw = await send_bounded(
                        self.provider, client, request, max_bytes=MAX_RESPONSE_BYTES
                    )
            try:
                payload = json.loads(raw)
            except ValueError as exc:
                raise ProviderResponseError(
                    self.provider, "response was not JSON"
                ) from exc
            if not isinstance(payload, dict):
                raise ProviderResponseError(self.provider, "unexpected response shape")
            return payload

        retries = (
            self._settings.website_research_max_retries
            if safe_retries_only
            else self._settings.research_max_retries
        )
        endpoint = _endpoint_label(url)
        started = time.monotonic()
        try:
            payload = await retry_async(
                once,
                provider=self.provider,
                max_retries=retries,
                base_delay=self._settings.research_retry_base_delay_seconds,
                max_delay=self._settings.research_retry_max_delay_seconds,
                sleep=self._sleep,
                rng=self._rng,
                retry_if=rate_limit_only if safe_retries_only else None,
            )
        except ProviderError as exc:
            self._record_call(endpoint, started, exc.code)
            raise
        self._record_call(endpoint, started, "ok")
        return payload

    def _record_call(self, endpoint: str, started: float, outcome: str) -> None:
        duration = int((time.monotonic() - started) * 1000)
        metrics.add_ms(f"firecrawl{endpoint}", duration)
        log_event(
            logger,
            "provider_call",
            provider=self.provider,
            operation=endpoint,
            status=outcome,
            duration_ms=duration,
        )

    async def map_site(
        self,
        url: str,
        *,
        search: str | None = None,
        limit: int = 200,
        include_subdomains: bool = False,
    ) -> list[MapLink]:
        """List URLs on a website via ``POST /v1/map`` (no page content).

        ``limit`` bounds the number of returned links. The returned URLs are
        untrusted: callers must validate each one before fetching it.
        """
        try:
            self._guard.normalize(url)
        except UrlRejected as exc:
            raise ProviderRequestError(
                self.provider, f"URL refused: {exc.reason}"
            ) from exc
        body: dict[str, Any] = {
            "url": url,
            "limit": max(1, limit),
            "includeSubdomains": include_subdomains,
        }
        if search:
            body["search"] = search
        payload = await self._request_json(
            "POST",
            FIRECRAWL_MAP_URL,
            body,
            read_timeout=self._settings.website_research_map_timeout_seconds,
            safe_retries_only=True,
        )
        links = payload.get("links")
        if payload.get("success") is not True or not isinstance(links, list):
            raise ProviderResponseError(self.provider, "map reported failure")
        result: list[MapLink] = []
        for item in links:
            if isinstance(item, str):
                link = _text(item, 2048)
                if link:
                    result.append(MapLink(url=link))
            elif isinstance(item, dict) and (link := _text(item.get("url"), 2048)):
                result.append(
                    MapLink(
                        url=link,
                        title=_text(item.get("title"), 500),
                        description=_text(item.get("description"), 1000),
                    )
                )
            if len(result) >= limit:
                break
        return result

    async def batch_scrape(self, urls: list[str]) -> list[ExtractedPage]:
        """Scrape ``urls`` through Firecrawl's batch API; results align with input.

        Starts an asynchronous job (``POST /v1/batch/scrape``) and polls
        ``GET /v1/batch/scrape/{id}`` a bounded number of times. Only results
        whose ``sourceURL`` is one we requested are accepted; URLs missing from
        the provider's answer become ``FAILED`` pages. Raises ``ProviderError``
        when the job cannot be started, fails, or does not finish in time (the
        caller may fall back to individual scraping).
        """
        for url in urls:
            try:
                self._guard.normalize(url)
            except UrlRejected as exc:
                raise ProviderRequestError(
                    self.provider, f"URL refused: {exc.reason}"
                ) from exc
        pages: dict[str, ExtractedPage] = {}
        try:
            # Hard overall deadline; also cancels an in-flight start or poll request.
            async with asyncio.timeout(
                self._settings.website_research_batch_timeout_seconds
            ):
                for start in range(0, len(urls), BATCH_CHUNK_SIZE):
                    chunk = urls[start : start + BATCH_CHUNK_SIZE]
                    pages.update(await self._batch_chunk(chunk))
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                self.provider, "batch job did not finish in time"
            ) from exc
        return [
            pages.get(_url_key(u))
            or self._failed(u, "URL missing from batch results", "missing_from_batch")
            for u in urls
        ]

    async def _batch_chunk(self, urls: list[str]) -> dict[str, ExtractedPage]:
        read = self._settings.firecrawl_scrape_timeout_seconds
        body = {
            "urls": urls,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": int(read * 900),
        }
        started = await self._request_json(
            "POST", FIRECRAWL_BATCH_URL, body, safe_retries_only=True
        )
        job_id = started.get("id")
        if started.get("success") is not True or not isinstance(job_id, str):
            raise ProviderResponseError(self.provider, "batch job was not started")
        status_url = f"{FIRECRAWL_BATCH_URL}/{job_id}"
        polls = self._settings.website_research_batch_max_polls
        for _ in range(polls):
            status = await self._request_json("GET", status_url)
            state = status.get("status")
            if state == "completed":
                return self._batch_pages(urls, status.get("data"))
            if state in ("failed", "cancelled"):
                raise ProviderUnavailableError(self.provider, f"batch job {state}")
            await self._sleep(self._settings.website_research_batch_poll_seconds)
        raise ProviderUnavailableError(
            self.provider, "batch job did not finish in time"
        )

    def _batch_pages(self, urls: list[str], data: Any) -> dict[str, ExtractedPage]:
        if not isinstance(data, list):
            raise ProviderResponseError(self.provider, "batch data malformed")
        wanted = {_url_key(u): u for u in urls}
        pages: dict[str, ExtractedPage] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            meta = item.get("metadata")
            source = (
                _text(meta.get("sourceURL"), 2048) if isinstance(meta, dict) else None
            )
            key = _url_key(source) if source else None
            if key not in wanted:  # unrequested result: never accept it
                continue
            pages[key] = self._to_page(wanted[key], item)
        return pages

    @staticmethod
    def _mark_failed(page: ExtractedPage, message: str, category: str) -> ExtractedPage:
        page.status = ExtractionStatus.FAILED
        page.error_message = message
        page.metadata["error_category"] = category
        return page

    @staticmethod
    def _failed(
        url: str, message: str, category: str, provider_status: int | None = None
    ) -> ExtractedPage:
        return ExtractedPage(
            original_url=url,
            status=ExtractionStatus.FAILED,
            error_message=message,
            provider_status=provider_status,
            metadata={"error_category": category},
        )
