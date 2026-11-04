"""DataForSEO adapter: keyword search volume, keyword difficulty and backlinks.

Endpoints (``POST``, HTTP Basic auth with ``DATAFORSEO_LOGIN``/``DATAFORSEO_PASSWORD``):

- ``/v3/keywords_data/google_ads/search_volume/live`` (monthly volume, CPC, competition)
- ``/v3/dataforseo_labs/google/bulk_keyword_difficulty/live`` (difficulty 0-100)
- ``/v3/backlinks/summary/live`` (referring domains and backlinks of a target)

Every request costs money, so callers pass an approved USD ceiling; the adapter
reports the ``cost`` DataForSEO returns and never retries a request that reached
the provider. Values are copied as returned; nothing is estimated.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from app.core.config import Settings
from app.tools.base import ExternalTool
from app.tools.errors import ProviderResponseError
from app.tools.http import build_timeout, client_scope, send_bounded

BASE_URL = "https://api.dataforseo.com"
MAX_KEYWORDS_PER_TASK = 700  # DataForSEO's per-task limit for search volume


def _basic(login: str, password: str) -> str:
    return base64.b64encode(f"{login}:{password}".encode()).decode()


class DataForSeoTool(ExternalTool):
    provider = "DataForSEO"
    required_settings = ("dataforseo_login", "dataforseo_password")

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(settings)
        self._client = client
        self.cost_usd = 0.0

    async def _post(self, path: str, task: dict[str, Any]) -> list[dict[str, Any]]:
        self.ensure_configured()
        password = self._settings.dataforseo_password
        assert password is not None and self._settings.dataforseo_login
        timeout = build_timeout(
            self._settings.research_http_connect_timeout_seconds,
            max(60.0, float(self._settings.research_http_read_timeout_seconds)),
        )
        async with client_scope(self._client, timeout) as client:
            request = client.build_request(
                "POST",
                BASE_URL + path,
                content=json.dumps([task]),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Basic "
                    + _basic(
                        self._settings.dataforseo_login, password.get_secret_value()
                    ),
                },
                timeout=timeout,
            )
            _, body = await send_bounded(self.provider, client, request)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise ProviderResponseError(self.provider, "response was not JSON") from exc
        status = payload.get("status_code") if isinstance(payload, dict) else None
        if status != 20000:
            raise ProviderResponseError(
                self.provider, f"request failed (status {status})"
            )
        self.cost_usd += float(payload.get("cost") or 0)
        tasks = payload.get("tasks") or []
        if not tasks or tasks[0].get("status_code") != 20000:
            raise ProviderResponseError(self.provider, "task failed")
        result = tasks[0].get("result") or []
        return [r for r in result if isinstance(r, dict)]

    async def search_volume(
        self, keywords: list[str], location_code: int, language_code: str = "en"
    ) -> list[dict[str, Any]]:
        """Rows with ``keyword``, ``search_volume``, ``cpc``, ``competition``,
        ``monthly_searches`` exactly as returned."""
        result = await self._post(
            "/v3/keywords_data/google_ads/search_volume/live",
            {
                "keywords": keywords[:MAX_KEYWORDS_PER_TASK],
                "location_code": location_code,
                "language_code": language_code,
            },
        )
        return result

    async def keyword_difficulty(
        self, keywords: list[str], location_code: int, language_code: str = "en"
    ) -> list[dict[str, Any]]:
        """Rows with ``keyword`` and ``keyword_difficulty`` (0-100)."""
        result = await self._post(
            "/v3/dataforseo_labs/google/bulk_keyword_difficulty/live",
            {
                "keywords": keywords[:1000],
                "location_code": location_code,
                "language_code": language_code,
            },
        )
        items: list[dict[str, Any]] = []
        for block in result:
            items += [i for i in block.get("items") or [] if isinstance(i, dict)]
        return items

    async def backlinks_summary(self, target: str) -> dict[str, Any]:
        """``referring_domains``, ``backlinks``, ``rank`` ... for a domain or URL."""
        result = await self._post(
            "/v3/backlinks/summary/live", {"target": target, "include_subdomains": True}
        )
        return result[0] if result else {}
