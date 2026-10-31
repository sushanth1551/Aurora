"""SerpApi adapter: Google organic search results via the SerpApi HTTP API.

Uses HTTPX against ``GET https://serpapi.com/search`` (``engine=google``,
``q``, ``num``, ``gl``, ``hl``, ``api_key``) instead of the official
``serpapi`` package, which is built on blocking ``requests`` and hides
``Retry-After``/status details needed for retries in an async service.

The API key travels as a query parameter (SerpApi's scheme), so this module
never logs request URLs or ``httpx`` exception text; ``httpx`` request logging
is also capped at WARNING by ``configure_logging``.
"""

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.concurrency import shared_gate
from app.core.config import Settings
from app.core.logging import install_secret_redaction, log_event
from app.schemas.research import SearchResult
from app.tools.base import ExternalTool
from app.tools.errors import ProviderResponseError
from app.tools.http import (
    build_timeout,
    client_scope,
    configure_circuit_breaker,
    retry_async,
    send_bounded,
)

logger = logging.getLogger(__name__)
install_secret_redaction()  # the API key is a URL query parameter

SERPAPI_URL = "https://serpapi.com/search"
MAX_NUM_RESULTS = 100
_NO_RESULTS_MARKER = "hasn't returned any results"


def _text(value: Any, limit: int) -> str | None:
    """A trimmed string of at most ``limit`` characters, or ``None``."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _host_of(link: str) -> str | None:
    """Lower-case host of the destination URL (``None`` if it has none)."""
    try:
        host = urlsplit(link).hostname
    except ValueError:
        return None
    return host[:253] if host else None


_SEARCH_WRAPPER_PREFIXES = ("/url", "/aclk", "/goto", "/search")


def _is_destination(link: str) -> bool:
    """True for an absolute http(s) URL that is not a Google redirect wrapper."""
    parts = urlsplit(link)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    host = parts.hostname.lower()
    on_google = host == "google.com" or host.endswith(".google.com")
    return not (on_google and parts.path.startswith(_SEARCH_WRAPPER_PREFIXES))


def _homepage_from_displayed(displayed: str | None) -> str | None:
    """Homepage URL from ``displayed_link`` (``https://host[ › path]``).

    Real SerpApi responses sometimes give ``link`` as a relative Google redirect
    (``/goto?url=...``); the only destination information left is the displayed
    host. Only the site root is returned - the exact page is unknown.
    """
    if not displayed:
        return None
    token = displayed.split()[0] if displayed.split() else ""
    parts = urlsplit(token)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return f"{parts.scheme}://{parts.netloc}/"[:2048]


class SerpApiTool(ExternalTool):
    """Retrieve normalized organic search results from SerpApi."""

    provider = "SerpApi"
    required_settings = ("serpapi_api_key",)

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
        configure_circuit_breaker(
            self.provider,
            self._settings.provider_circuit_failure_threshold,
            self._settings.provider_circuit_cooldown_seconds,
        )

    async def search(
        self,
        query: str,
        *,
        num_results: int = 10,
        country: str | None = None,
        language: str | None = None,
    ) -> list[SearchResult]:
        """Return up to ``num_results`` organic results for ``query``.

        Raises ``ConfigurationException`` without a key, and ``ProviderError``
        subclasses for authentication, rate-limit, availability, or response
        problems. Transient failures are retried with exponential backoff and
        jitter (honouring ``Retry-After``); permanent ones are not.
        """
        self.ensure_configured()
        count = max(1, min(num_results, MAX_NUM_RESULTS))
        params = self._build_params(query, count, country, language)
        started = time.monotonic()
        payload = await retry_async(
            lambda: self._fetch(params),
            provider=self.provider,
            max_retries=self._settings.research_max_retries,
            base_delay=self._settings.research_retry_base_delay_seconds,
            max_delay=self._settings.research_retry_max_delay_seconds,
            sleep=self._sleep,
            rng=self._rng,
        )
        results = self._parse(payload, count)
        log_event(
            logger,
            "provider_call",
            provider=self.provider,
            operation="search",
            status="ok",
            results=len(results),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return results

    def _build_params(
        self, query: str, num: int, country: str | None, language: str | None
    ) -> dict[str, str]:
        key = self._settings.serpapi_api_key
        assert key is not None  # guaranteed by ensure_configured()
        params = {
            "engine": "google",
            "q": query,
            "num": str(num),
            "output": "json",
            "api_key": key.get_secret_value(),
        }
        if country:
            params["gl"] = country
        if language:
            params["hl"] = language
        return params

    async def _fetch(self, params: dict[str, str]) -> dict[str, Any]:
        timeout = build_timeout(
            self._settings.research_http_connect_timeout_seconds,
            self._settings.research_http_read_timeout_seconds,
        )
        async with client_scope(self._client, timeout) as client:
            request = client.build_request(
                "GET", SERPAPI_URL, params=params, timeout=timeout
            )
            async with shared_gate(
                "serpapi", self._settings.serpapi_max_concurrent_requests
            ):
                _, body = await send_bounded(self.provider, client, request)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise ProviderResponseError(self.provider, "response was not JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderResponseError(self.provider, "unexpected response shape")
        error = payload.get("error")
        if isinstance(error, str) and _NO_RESULTS_MARKER not in error:
            raise ProviderResponseError(self.provider, "search reported an error")
        return payload

    def _parse(self, payload: dict[str, Any], limit: int) -> list[SearchResult]:
        organic = payload.get("organic_results")
        if organic is None:
            return []
        if not isinstance(organic, list):
            raise ProviderResponseError(self.provider, "organic_results malformed")
        metadata = payload.get("search_metadata")
        search_id = (
            _text(metadata.get("id"), 100) if isinstance(metadata, dict) else None
        )
        results: list[SearchResult] = []
        for index, item in enumerate(organic, start=1):
            if not isinstance(item, dict):
                continue
            link = _text(item.get("link"), 2048)
            extra: dict[str, Any] = {"engine": "google"}
            if link is None or not _is_destination(link):
                link = _homepage_from_displayed(_text(item.get("displayed_link"), 1000))
                if link is None:
                    continue
                extra["destination_source"] = "displayed_link"  # homepage only
            position = item.get("position")
            if search_id:
                extra["search_id"] = search_id
            if publisher := _text(item.get("source"), 200):
                extra["publisher"] = publisher  # a name such as "Apple", not a domain
            if date := _text(item.get("date"), 100):
                extra["date"] = date
            results.append(
                SearchResult(
                    position=position
                    if isinstance(position, int) and position > 0
                    else index,
                    title=_text(item.get("title"), 1000) or "",
                    url=link,
                    snippet=_text(item.get("snippet"), 5000),
                    displayed_link=_text(item.get("displayed_link"), 1000),
                    domain=_host_of(link),
                    metadata=extra,
                )
            )
            if len(results) >= limit:
                break
        return results
