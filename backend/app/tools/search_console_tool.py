"""Google Search Console adapter (read-only).

Uses HTTPX against the documented Google endpoints: the OAuth token endpoint and
the Search Console API v3 (``sites.list``, ``searchAnalytics.query``). No Google
SDK is installed. Only the ``webmasters.readonly`` scope is ever requested.

- The refresh token comes from an injected provider (the stored, vault-protected
  token, else ``GOOGLE_REFRESH_TOKEN``). Access tokens live in memory only and are
  refreshed on demand; one 401 triggers one refresh and one retry.
- ``invalid_grant`` from the token endpoint means the authorization was revoked or
  expired: it raises ``GscReauthorizationRequired`` so the UI can ask to reconnect.
- Search Analytics reads are idempotent, so transient failures are retried with
  backoff. Google quota errors arrive as HTTP 403 and are reported as "access
  denied or quota exceeded" without automatic retry (the status-only mapping of
  ``tools.http`` cannot tell the reasons apart).
- Nothing here logs tokens, client secrets, request bodies or URLs.
"""

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from app.core.concurrency import shared_gate
from app.core.config import Settings
from app.core.exceptions import ConfigurationException
from app.core.logging import log_event
from app.monitoring.errors import (
    GscNotConnected,
    GscOAuthError,
    GscReauthorizationRequired,
)
from app.monitoring.oauth import REVOKE_URL, SCOPE, TOKEN_URL
from app.tools.base import ExternalTool
from app.tools.errors import (
    ProviderAuthenticationError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.tools.http import (
    build_timeout,
    client_scope,
    configure_circuit_breaker,
    retry_async,
    send_bounded,
)

logger = logging.getLogger(__name__)

SITES_URL = "https://www.googleapis.com/webmasters/v3/sites"
VERIFIED_LEVELS = frozenset({"siteOwner", "siteFullUser", "siteRestrictedUser"})
DIMENSIONS = frozenset(
    {"date", "query", "page", "country", "device", "searchAppearance"}
)
SEARCH_TYPES = frozenset({"web", "image", "video", "news", "discover", "googleNews"})
PROPERTY_RE = re.compile(
    r"^(sc-domain:[A-Za-z0-9.-]{1,253}|https?://[^\s/]{1,253}/[^\s]{0,500})$"
)
TOKEN_SKEW_SECONDS = 60
MAX_RESPONSE_BYTES = 16 * 1024 * 1024  # 25,000 rows of three keys fit comfortably
_OAUTH_ERRORS = frozenset(
    {
        "invalid_grant",
        "invalid_client",
        "unauthorized_client",
        "invalid_request",
        "invalid_scope",
        "access_denied",
        "redirect_uri_mismatch",
    }
)

RefreshTokenProvider = Callable[[], Awaitable[str | None]]


class SiteEntry(BaseModel):
    site_url: str
    permission_level: str
    verified: bool


class PerfRow(BaseModel):
    """One Search Analytics row; ``keys`` align with the requested dimensions."""

    keys: list[str]
    clicks: float
    impressions: float
    ctr: float
    position: float


class TokenGrant(BaseModel):
    refresh_token: str
    scope: str


def validate_property(property_id: str) -> str:
    if not PROPERTY_RE.match(property_id):
        raise ProviderRequestError(
            "Google Search Console",
            "property must be 'sc-domain:example.com' or a URL-prefix like 'https://example.com/'",
        )
    return property_id


def validate_dates(
    start: str, end: str, *, max_days: int, today: date | None = None
) -> None:
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise ProviderRequestError(
            "Google Search Console", "dates must be YYYY-MM-DD"
        ) from exc
    now = today or date.today()
    if first > last:
        raise ProviderRequestError(
            "Google Search Console", "start date is after end date"
        )
    if last > now:
        raise ProviderRequestError("Google Search Console", "end date is in the future")
    if first < now - timedelta(days=max_days):
        raise ProviderRequestError(
            "Google Search Console", f"start date is older than {max_days} days"
        )


class SearchConsoleTool(ExternalTool):
    """Read Search Console data for properties the authorized user can access."""

    provider = "Google Search Console"
    required_settings = ("google_client_id", "google_client_secret")

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        refresh_token_provider: RefreshTokenProvider | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(settings)
        self._client = client
        self._sleep = sleep
        self._rng = rng
        self._clock = clock
        self._provider = refresh_token_provider or self._env_refresh_token
        self._access: tuple[str, float] | None = None
        configure_circuit_breaker(
            self.provider,
            self._settings.provider_circuit_failure_threshold,
            self._settings.provider_circuit_cooldown_seconds,
        )

    async def _env_refresh_token(self) -> str | None:
        token = self._settings.google_refresh_token
        return token.get_secret_value() if token else None

    @property
    def redirect_uri(self) -> str:
        return self._settings.google_oauth_redirect_uri

    @property
    def client_id(self) -> str:
        client_id = self._settings.google_client_id
        if not client_id:
            raise ConfigurationException(
                f"{self.provider} is not configured; set GOOGLE_CLIENT_ID."
            )
        return client_id

    # -- OAuth token endpoint ---------------------------------------------------------

    async def _token_post(
        self, data: dict[str, str], url: str = TOKEN_URL
    ) -> dict[str, Any]:
        """POST a form to Google's OAuth endpoint; map failures without echoing bodies."""
        timeout = build_timeout(
            self._settings.research_http_connect_timeout_seconds,
            self._settings.research_http_read_timeout_seconds,
        )

        async def once() -> dict[str, Any]:
            async with client_scope(self._client, timeout) as client:
                try:
                    response = await client.post(
                        url, data=data, timeout=timeout, follow_redirects=False
                    )
                except httpx.TimeoutException as exc:
                    raise ProviderTimeoutError(
                        self.provider, "token request timed out"
                    ) from exc
                except httpx.HTTPError as exc:
                    raise ProviderUnavailableError(
                        self.provider, f"network error ({type(exc).__name__})"
                    ) from exc
            try:
                body = response.json()
            except ValueError:
                body = {}
            error = body.get("error") if isinstance(body, dict) else None
            code = error if isinstance(error, str) and error in _OAUTH_ERRORS else None
            if response.status_code >= 500 or response.status_code == 429:
                raise ProviderUnavailableError(
                    self.provider,
                    f"temporary failure (HTTP {response.status_code})",
                    status_code=response.status_code,
                )
            if response.status_code >= 400:
                if code == "invalid_grant":
                    raise GscReauthorizationRequired()
                if code in ("invalid_client", "unauthorized_client"):
                    raise ProviderAuthenticationError(
                        self.provider,
                        "Google rejected the OAuth client; check GOOGLE_CLIENT_ID and "
                        "GOOGLE_CLIENT_SECRET",
                        status_code=response.status_code,
                    )
                raise GscOAuthError(
                    f"Google refused the authorization request ({code or 'error'})."
                    + (
                        " Check that the redirect URI matches the OAuth client."
                        if code == "redirect_uri_mismatch"
                        else ""
                    )
                )
            if not isinstance(body, dict):
                raise ProviderResponseError(self.provider, "unexpected token response")
            return body

        return await retry_async(
            once,
            provider=self.provider,
            max_retries=self._settings.research_max_retries,
            base_delay=self._settings.research_retry_base_delay_seconds,
            max_delay=self._settings.research_retry_max_delay_seconds,
            sleep=self._sleep,
            rng=self._rng,
        )

    def _client_credentials(self) -> dict[str, str]:
        secret = self._settings.google_client_secret
        if secret is None:
            raise ConfigurationException(
                f"{self.provider} is not configured; set GOOGLE_CLIENT_SECRET."
            )
        return {"client_id": self.client_id, "client_secret": secret.get_secret_value()}

    async def exchange_code(self, code: str, verifier: str) -> TokenGrant:
        """Exchange an authorization code (with the PKCE verifier) for a refresh token."""
        self.ensure_configured()
        try:
            body = await self._token_post(
                {
                    **self._client_credentials(),
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": self.redirect_uri,
                    "grant_type": "authorization_code",
                }
            )
        except GscReauthorizationRequired as exc:
            # For a new code, invalid_grant means the code is bad, expired or already used.
            raise GscOAuthError(
                "Google rejected the authorization code (it may have expired or been "
                "used). Start the connection again."
            ) from exc
        refresh = body.get("refresh_token")
        scope = body.get("scope")
        if not isinstance(refresh, str) or not refresh:
            raise GscOAuthError(
                "Google returned no refresh token. Remove AURORA from your Google account's "
                "third-party access and connect again."
            )
        granted = set(scope.split()) if isinstance(scope, str) else set()
        if SCOPE not in granted:
            raise GscOAuthError(
                "Google did not grant the read-only Search Console scope."
            )
        extra = granted - {SCOPE}
        if extra:  # we only asked for one scope; refuse anything broader
            raise GscOAuthError("Google granted more access than AURORA requested.")
        return TokenGrant(refresh_token=refresh, scope=" ".join(sorted(granted)))

    async def revoke(self, token: str) -> None:
        """Ask Google to revoke a token (best effort; failures are ignored by callers)."""
        await self._token_post({"token": token}, url=REVOKE_URL)

    async def _access_token(self, *, force_refresh: bool = False) -> str:
        self.ensure_configured()
        if not force_refresh and self._access and self._access[1] > self._clock():
            return self._access[0]
        refresh = await self._provider()
        if not refresh:
            raise GscNotConnected()
        body = await self._token_post(
            {
                **self._client_credentials(),
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            }
        )
        token = body.get("access_token")
        expires = body.get("expires_in")
        if not isinstance(token, str) or not token:
            raise ProviderResponseError(
                self.provider, "token response had no access token"
            )
        seconds = expires if isinstance(expires, int | float) else 300
        self._access = (token, self._clock() + max(0, seconds - TOKEN_SKEW_SECONDS))
        return token

    def forget_access_token(self) -> None:
        self._access = None

    async def verify_connection(self) -> None:
        """Refresh the access token (one Google call); raises if the grant is unusable."""
        await self._access_token(force_refresh=True)

    # -- Search Console API ---------------------------------------------------------------

    async def _api(
        self, method: str, url: str, body: dict[str, Any] | None = None
    ) -> Any:
        timeout = build_timeout(
            self._settings.research_http_connect_timeout_seconds,
            self._settings.research_http_read_timeout_seconds,
        )

        async def once(force: bool) -> Any:
            token = await self._access_token(force_refresh=force)
            async with client_scope(self._client, timeout) as client:
                request = client.build_request(
                    method,
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=timeout,
                )
                async with shared_gate(
                    "gsc", self._settings.serpapi_max_concurrent_requests
                ):
                    _, raw = await send_bounded(
                        self.provider, client, request, max_bytes=MAX_RESPONSE_BYTES
                    )
            try:
                return json.loads(raw) if raw else {}
            except ValueError as exc:
                raise ProviderResponseError(
                    self.provider, "response was not JSON"
                ) from exc

        async def with_reauth() -> Any:
            try:
                return await once(False)
            except ProviderAuthenticationError as exc:
                if exc.provider_status != 401:
                    raise
                self.forget_access_token()  # expired or revoked access token: refresh once
                return await once(True)

        started = time.monotonic()
        result = await retry_async(
            with_reauth,
            provider=self.provider,
            max_retries=self._settings.research_max_retries,
            base_delay=self._settings.research_retry_base_delay_seconds,
            max_delay=self._settings.research_retry_max_delay_seconds,
            sleep=self._sleep,
            rng=self._rng,
        )
        log_event(
            logger,
            "provider_call",
            provider=self.provider,
            operation="search_console",
            status="ok",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    async def list_sites(self) -> list[SiteEntry]:
        """Properties the authorized account can see; ``verified`` excludes unverified users."""
        self.ensure_configured()
        data = await self._api("GET", SITES_URL)
        entries = data.get("siteEntry") if isinstance(data, dict) else None
        out: list[SiteEntry] = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            url, level = entry.get("siteUrl"), entry.get("permissionLevel")
            if isinstance(url, str) and isinstance(level, str):
                out.append(
                    SiteEntry(
                        site_url=url,
                        permission_level=level,
                        verified=level in VERIFIED_LEVELS,
                    )
                )
        return sorted(out, key=lambda s: s.site_url)

    async def query_performance(
        self,
        property_id: str,
        start_date: str,
        end_date: str,
        *,
        dimensions: tuple[str, ...] = ("date", "query", "page"),
        row_limit: int | None = None,
        start_row: int = 0,
        search_type: str = "web",
        data_state: str = "final",
    ) -> list[PerfRow]:
        """One Search Analytics request (one page of rows, ``start_row`` based).

        Rows are exactly what Google returned. Queries Google anonymizes are omitted
        from query-level rows by Google, so row sums can be below the property
        totals (request ``("date",)`` for totals).
        """
        self.ensure_configured()
        validate_property(property_id)
        validate_dates(
            start_date, end_date, max_days=self._settings.gsc_max_history_days
        )
        if (
            not dimensions
            or not set(dimensions) <= DIMENSIONS
            or len(set(dimensions)) != len(dimensions)
        ):
            raise ProviderRequestError(self.provider, "invalid dimensions")
        if search_type not in SEARCH_TYPES:
            raise ProviderRequestError(self.provider, "invalid search type")
        if data_state not in ("final", "all"):
            raise ProviderRequestError(self.provider, "invalid data state")
        limit = min(
            row_limit or self._settings.gsc_max_rows_per_request,
            self._settings.gsc_max_rows_per_request,
        )
        url = f"{SITES_URL}/{quote(property_id, safe='')}/searchAnalytics/query"
        data = await self._api(
            "POST",
            url,
            {
                "startDate": start_date,
                "endDate": end_date,
                "dimensions": list(dimensions),
                "rowLimit": limit,
                "startRow": max(0, start_row),
                "searchType": search_type,
                "dataState": data_state,
            },
        )
        raw_rows = data.get("rows") if isinstance(data, dict) else None
        rows: list[PerfRow] = []
        for item in raw_rows if isinstance(raw_rows, list) else []:
            if not isinstance(item, dict):
                continue
            keys = item.get("keys")
            clicks, impressions = _number(item, "clicks"), _number(item, "impressions")
            ctr, position = _number(item, "ctr"), _number(item, "position")
            if (
                isinstance(keys, list)
                and len(keys) == len(dimensions)
                and all(isinstance(k, str) for k in keys)
                and None not in (clicks, impressions, ctr, position)
            ):
                rows.append(
                    PerfRow(
                        keys=keys,
                        clicks=clicks or 0.0,
                        impressions=impressions or 0.0,
                        ctr=ctr or 0.0,
                        position=position or 0.0,
                    )
                )
        return rows


def _number(item: dict[str, Any], key: str) -> float | None:
    """A JSON number as float; booleans, strings and missing values are rejected."""
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
