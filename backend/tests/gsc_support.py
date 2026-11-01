"""Offline Google doubles for Search Console tests: a scripted Google (respx), a fake
vault and builders. Nothing here reaches the network; secrets are obviously fake."""

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, unquote
from uuid import uuid4

import httpx
import respx
from pydantic import SecretStr

from app.core.config import Settings
from app.database.models import GscSyncRunRecord
from app.monitoring.oauth import SCOPE, TOKEN_URL
from app.schemas.research import utc_now
from app.tools.search_console_tool import SITES_URL

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "test-client-secret-XYZ"
REFRESH_TOKEN = "1//REFRESH-TOKEN-SECRET-123"
PROPERTY = "sc-domain:example.com"
PREFIX_PROPERTY = "https://www.example.com/"
SITE = "https://www.example.com"
TODAY = date.today()
END = TODAY - timedelta(days=3)  # latest final day


def settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "google_client_id": CLIENT_ID,
        "google_client_secret": SecretStr(CLIENT_SECRET),
        "research_max_retries": 1,
        "gsc_max_rows_per_request": 100,
        "gsc_max_requests_per_sync": 6,
    }
    base.update(overrides)
    return Settings(**base)


class FakeVault:
    """Reversible stand-in for DPAPI: the stored value is clearly not the secret."""

    name = "fake"

    def seal(self, secret: str) -> str:
        return "sealed:" + base64.b64encode(secret[::-1].encode()).decode()

    def open(self, sealed: str) -> str:
        assert sealed.startswith("sealed:")
        return base64.b64decode(sealed[7:]).decode()[::-1]


def row(
    day: str, query: str, page: str, clicks=1.0, impressions=10.0, position=3.0
) -> dict[str, Any]:
    return {
        "keys": [day, query, page],
        "clicks": clicks,
        "impressions": impressions,
        "ctr": clicks / impressions if impressions else 0.0,
        "position": position,
    }


def daily(day: str, clicks=10.0, impressions=100.0, position=3.0) -> dict[str, Any]:
    return {
        "keys": [day],
        "clicks": clicks,
        "impressions": impressions,
        "ctr": clicks / impressions,
        "position": position,
    }


def days(end: date, count: int) -> list[str]:
    return [(end - timedelta(days=i)).isoformat() for i in range(count - 1, -1, -1)]


@dataclass
class FakeGoogle:
    """Scripts Google's OAuth and Search Console endpoints.

    ``detail_rows``/``daily_rows`` are served by ``startRow``/``rowLimit``. A request
    with a stale access token gets 401 (call ``expire_access_token``).
    """

    mock: respx.MockRouter
    sites: list[dict[str, str]] = field(
        default_factory=lambda: [
            {"siteUrl": PROPERTY, "permissionLevel": "siteOwner"},
            {"siteUrl": PREFIX_PROPERTY, "permissionLevel": "siteUnverifiedUser"},
        ]
    )
    daily_rows: list[dict[str, Any]] = field(default_factory=list)
    detail_rows: list[dict[str, Any]] = field(default_factory=list)
    token_forms: list[dict[str, str]] = field(default_factory=list)
    query_bodies: list[dict[str, Any]] = field(default_factory=list)
    query_paths: list[str] = field(default_factory=list)
    auth_headers: list[str] = field(default_factory=list)
    token_error: str | None = None
    reject_auth: bool = False  # Google answers 401 even for fresh tokens
    token_status: int = 400
    code_scope: str = SCOPE
    code_refresh_token: str | None = "1//NEW-REFRESH-TOKEN-456"
    query_statuses: list[int] = field(
        default_factory=list
    )  # consumed one per query call
    _issued: int = 0
    _valid: str | None = None

    def __post_init__(self) -> None:
        self.mock.post(TOKEN_URL).mock(side_effect=self._token)
        self.mock.get(SITES_URL).mock(side_effect=self._sites)
        self.mock.post(url__startswith=SITES_URL + "/").mock(side_effect=self._query)

    # -- scripted behaviour ---------------------------------------------------------------

    def expire_access_token(self) -> None:
        self._valid = None

    @property
    def refresh_calls(self) -> int:
        return sum(
            1 for f in self.token_forms if f.get("grant_type") == "refresh_token"
        )

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.token_forms.append(form)
        if self.token_error:
            return httpx.Response(
                self.token_status,
                json={"error": self.token_error, "error_description": "secret detail"},
            )
        if form.get("grant_type") == "authorization_code":
            body: dict[str, Any] = {
                "access_token": self._new_token(),
                "expires_in": 3600,
                "scope": self.code_scope,
                "token_type": "Bearer",
            }
            if self.code_refresh_token:
                body["refresh_token"] = self.code_refresh_token
            return httpx.Response(200, json=body)
        return httpx.Response(
            200,
            json={
                "access_token": self._new_token(),
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    def _new_token(self) -> str:
        self._issued += 1
        self._valid = f"access-token-{self._issued}"
        return self._valid

    def _authorized(self, request: httpx.Request) -> bool:
        header = request.headers.get("authorization", "")
        self.auth_headers.append(header)
        return (
            (not self.reject_auth)
            and self._valid is not None
            and header == f"Bearer {self._valid}"
        )

    def _sites(self, request: httpx.Request) -> httpx.Response:
        if not self._authorized(request):
            return httpx.Response(401, json={"error": {"code": 401}})
        return httpx.Response(200, json={"siteEntry": self.sites})

    def _query(self, request: httpx.Request) -> httpx.Response:
        if not self._authorized(request):
            return httpx.Response(401, json={"error": {"code": 401}})
        if self.query_statuses:
            status = self.query_statuses.pop(0)
            if status != 200:
                return httpx.Response(status, json={"error": {"code": status}})
        body = json.loads(request.content)
        self.query_bodies.append(body)
        self.query_paths.append(unquote(request.url.path))
        source = self.daily_rows if body["dimensions"] == ["date"] else self.detail_rows
        start, limit = body.get("startRow", 0), body["rowLimit"]
        return httpx.Response(200, json={"rows": source[start : start + limit]})


def install(mock: respx.MockRouter, **kwargs: Any) -> FakeGoogle:
    return FakeGoogle(mock, **kwargs)


Sleep = Callable[[float], Any]


def days_between(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


async def seed_two_windows(service, *, previous: bool = True) -> None:
    """56 days ending END: healthy baseline pages plus one page for each detector."""
    repo = service.repository
    cur_end = END
    cur_start = cur_end - timedelta(days=27)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=27)
    sync = GscSyncRunRecord(
        id=uuid4(),
        property=PROPERTY,
        start_date=prev_start,
        end_date=cur_end,
        status="completed",
        source="test",
        limits={},
        estimate={},
        usage={},
        created_at=utc_now(),
    )
    await repo.create_sync(sync)
    rows: list[dict] = []

    def add(
        window: str,
        page: str,
        query: str,
        clicks: float,
        impressions: float,
        position: float,
    ) -> None:
        start, end = (cur_start, cur_end) if window == "cur" else (prev_start, prev_end)
        for day in days_between(start, end):
            rows.append(
                {
                    "date": day,
                    "query": query,
                    "page": f"{SITE}{page}",
                    "search_type": "web",
                    "clicks": clicks,
                    "impressions": impressions,
                    "ctr": clicks / impressions,
                    "position": position,
                }
            )

    windows = ("cur", "prev") if previous else ("cur",)
    for w in windows:
        for i in range(30):  # the property's own baseline: 10% CTR at position 3
            add(w, f"/healthy-{i}", f"healthy {i}", 1, 10, 3.0)
        # present in both windows, so they are not "new queries"
        add(w, "/low-ctr", "slow query", 3, 100, 3.0)  # 3% where the property gets ~10%
        add(w, "/a", "shared query", 1, 20, 5.0)  # cannibalization
        add(w, "/b", "shared query", 1, 15, 7.0)
    # the only query that appears for the first time in the current window
    add("cur", "/healthy-0", "brand new query", 0.1, 5, 8.0)
    if previous:
        add("prev", "/declining", "declining query", 20, 400, 3.0)
        add("cur", "/declining", "declining query", 8, 200, 6.5)
        add("prev", "/growing", "growing query", 2, 40, 5.0)
        add("cur", "/growing", "growing query", 4, 60, 4.0)
    else:
        add("cur", "/declining", "declining query", 8, 200, 6.5)
    await repo.insert_performance(PROPERTY, rows, sync.id)
    totals: dict[date, list[float]] = {}
    for r in rows:
        t = totals.setdefault(r["date"], [0.0, 0.0, 0.0])
        t[0] += r["clicks"]
        t[1] += r["impressions"]
        t[2] += r["position"] * r["impressions"]
    await repo.insert_daily(
        PROPERTY,
        [(d, c, i, c / i, w / i) for d, (c, i, w) in sorted(totals.items())],
        sync.id,
    )
