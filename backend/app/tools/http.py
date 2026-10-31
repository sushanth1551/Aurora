"""HTTP helpers for provider adapters: bounded retries and bounded bodies."""

import asyncio
import email.utils
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx

from app.core import metrics
from app.core.logging import log_event
from app.tools.errors import (
    ProviderAuthenticationError,
    ProviderBudgetError,
    ProviderCircuitOpenError,
    ProviderError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)

MAX_RETRY_AFTER_SECONDS = 30.0
DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def rate_limit_only(exc: ProviderError) -> bool:
    """Retry filter for non-idempotent calls: a 429 was rejected before processing."""
    return isinstance(exc, ProviderRateLimitError)


def build_timeout(connect: float, read: float) -> httpx.Timeout:
    """Explicit connect/read/write/pool timeouts for provider clients."""
    return httpx.Timeout(connect=connect, read=read, write=connect, pool=connect)


@asynccontextmanager
async def client_scope(
    client: httpx.AsyncClient | None, timeout: httpx.Timeout
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield the injected client, or a short-lived one that is closed afterwards."""
    if client is not None:
        yield client
        return
    async with httpx.AsyncClient(timeout=timeout) as created:
        yield created


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (seconds or HTTP date) into seconds."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = email.utils.parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError):
            return None
    return max(0.0, seconds)


async def retry_async[T](
    operation: Callable[[], Awaitable[T]],
    *,
    provider: str,
    max_retries: int,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
    retry_if: Callable[[ProviderError], bool] | None = None,
) -> T:
    """Run ``operation``, retrying only retryable ``ProviderError``s.

    ``retry_if`` can narrow retries further (e.g. rate limits only).
    Waits exponentially (``base_delay * 2**attempt``, capped) with full jitter,
    but never less than a server-supplied ``Retry-After`` (capped at 30s).
    At most ``max_retries`` retries are made; other errors propagate at once.
    """
    rand = rng or random.Random()  # noqa: S311 - jitter, not security
    attempt = 0
    while True:
        try:
            return await operation()
        except ProviderError as exc:
            if not exc.retryable or attempt >= max_retries:
                raise
            if retry_if is not None and not retry_if(exc):
                raise
            backoff = rand.uniform(0, min(max_delay, base_delay * 2**attempt))
            delay = backoff
            if exc.retry_after is not None:
                delay = max(backoff, min(exc.retry_after, MAX_RETRY_AFTER_SECONDS))
            attempt += 1
            log_event(
                logger,
                "provider_retry",
                level=logging.WARNING,
                provider=provider,
                attempt=attempt,
                max_retries=max_retries,
                delay=round(delay, 2),
                error_category=exc.code,
            )
            await sleep(delay)


def raise_for_provider_status(provider: str, response: httpx.Response) -> None:
    """Map non-success HTTP statuses to the provider error taxonomy."""
    status = response.status_code
    if status < 400:
        return
    retry_after = parse_retry_after(response.headers.get("Retry-After"))
    if status in (401, 402, 403):
        raise ProviderAuthenticationError(
            provider, "credentials rejected or access denied", status_code=status
        )
    if status == 429:
        raise ProviderRateLimitError(
            provider, "rate limited", status_code=status, retry_after=retry_after
        )
    if status in (408, 425) or status >= 500:
        raise ProviderUnavailableError(
            provider,
            f"temporary provider failure (HTTP {status})",
            status_code=status,
            retry_after=retry_after,
        )
    raise ProviderRequestError(
        provider, f"request rejected (HTTP {status})", status_code=status
    )


class CircuitBreaker:
    """Pauses calls to a provider after repeated outages (timeouts, 5xx, network).

    Closed -> open after ``threshold`` consecutive failures; after ``cooldown``
    seconds one probe request is allowed (half-open). Auth, request and
    response errors prove the provider is reachable and do not count.
    """

    def __init__(
        self,
        threshold: int,
        cooldown: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold, self.cooldown, self._clock = threshold, cooldown, clock
        self.failures = 0
        self.opened_at: float | None = None
        self.probing = False

    def allow(self) -> bool:
        if self.threshold <= 0 or self.opened_at is None:
            return True
        if self._clock() - self.opened_at < self.cooldown or self.probing:
            return False
        self.probing = True
        return True

    def success(self) -> None:
        self.failures, self.opened_at, self.probing = 0, None, False

    def failure(self) -> None:
        self.probing = False
        self.failures += 1
        if self.threshold > 0 and self.failures >= self.threshold:
            self.opened_at = self._clock()

    def abort_probe(self) -> None:
        self.probing = False


_breakers: dict[str, CircuitBreaker] = {}


def circuit_breaker(provider: str) -> CircuitBreaker:
    """The process-wide breaker for ``provider`` (thresholds come from settings)."""
    if provider not in _breakers:
        from app.core.config import get_settings

        cfg = get_settings()
        _breakers[provider] = CircuitBreaker(
            cfg.provider_circuit_failure_threshold,
            cfg.provider_circuit_cooldown_seconds,
        )
    return _breakers[provider]


def configure_circuit_breaker(provider: str, threshold: int, cooldown: float) -> None:
    """Apply a provider adapter's configured thresholds to the shared breaker."""
    breaker = circuit_breaker(provider)
    breaker.threshold, breaker.cooldown = threshold, cooldown


def reset_circuit_breakers() -> None:
    """Forget all breaker state (used by tests)."""
    _breakers.clear()


async def send_bounded(
    provider: str,
    client: httpx.AsyncClient,
    request: httpx.Request,
    *,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> tuple[httpx.Response, bytes]:
    """Send ``request`` through the circuit breaker and the per-run request budget.

    Cancelling the calling task cancels the in-flight HTTP request.
    """
    breaker = circuit_breaker(provider)
    if not breaker.allow():
        raise ProviderCircuitOpenError(
            provider, "temporarily paused after repeated failures"
        )
    if not metrics.reserve_request(provider):
        breaker.abort_probe()
        raise ProviderBudgetError(provider, "per-run request budget reached")
    try:
        result = await _send_bounded(provider, client, request, max_bytes=max_bytes)
    except ProviderUnavailableError:
        breaker.failure()
        raise
    except ProviderError:
        breaker.success()
        raise
    except BaseException:
        breaker.abort_probe()  # cancelled or unexpected: no verdict on the provider
        raise
    breaker.success()
    return result


async def _send_bounded(
    provider: str,
    client: httpx.AsyncClient,
    request: httpx.Request,
    *,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> tuple[httpx.Response, bytes]:
    """Send ``request`` and read at most ``max_bytes`` of the body.

    Transport problems become ``ProviderUnavailableError`` (retryable) without
    echoing the URL; oversized bodies become ``ProviderResponseError``.
    """
    try:
        response = await client.send(request, stream=True, follow_redirects=False)
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError(provider, "request timed out") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError(
            provider, f"network error ({type(exc).__name__})"
        ) from exc
    try:
        raise_for_provider_status(provider, response)
        chunks: list[bytes] = []
        size = 0
        try:
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ProviderResponseError(
                        provider, "response exceeded the size limit"
                    )
                chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                provider, f"network error ({type(exc).__name__})"
            ) from exc
        return response, b"".join(chunks)
    finally:
        await response.aclose()
