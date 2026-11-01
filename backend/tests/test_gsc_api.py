"""Search Console HTTP API on SQLite with a scripted Google (offline)."""

from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi import Request

from app.api.deps import get_gsc_service
from app.core.config import Settings
from app.core.rate_limit import RateLimits
from app.database.repositories.gsc_repository import GscRepository
from app.main import create_app
from app.monitoring.service import GscService, RefreshTokenSource
from app.schemas.research import utc_now
from app.tools.search_console_tool import SearchConsoleTool
from tests.auth_support import AUTH_HEADERS
from tests.gsc_support import (
    CLIENT_SECRET,
    END,
    PROPERTY,
    SITE,
    TODAY,
    FakeVault,
    daily,
    install,
    row,
    seed_two_windows,
    settings,
)

BASE = "/api/v1/gsc"


@pytest.fixture
def google():
    with respx.mock(assert_all_called=False) as mock:
        yield install(mock)


async def _no_sleep(_: float) -> None:
    return None


def make_app(db_session, **overrides):
    app = create_app()
    cfg = settings(**overrides)

    def build(request: Request) -> GscService:
        repo = GscRepository(db_session)
        vault = FakeVault()
        tokens = RefreshTokenSource(repo, vault, cfg)
        tool = SearchConsoleTool(
            cfg, refresh_token_provider=tokens.get, sleep=_no_sleep
        )
        return GscService(
            repo=repo,
            settings=cfg,
            tool=tool,
            tokens=tokens,
            vault=vault,
            flows=request.app.state.gsc_oauth_flows,
            today=lambda: TODAY,
        )

    app.dependency_overrides[get_gsc_service] = build
    return app


def client(app, headers=AUTH_HEADERS) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    )


async def authorize(api: httpx.AsyncClient) -> None:
    start = (await api.post(f"{BASE}/oauth/start")).json()
    state = parse_qs(urlsplit(start["authorization_url"]).query)["state"][0]
    done = await api.get(
        f"{BASE}/oauth/callback",
        params={"code": "c", "state": state},
        headers={"Authorization": ""},
    )
    assert done.status_code == 200


# -- authentication ----------------------------------------------------------------------------------


async def test_every_search_console_route_requires_the_token_except_the_oauth_callback(
    db_session,
) -> None:
    app = make_app(db_session)
    paths = [
        (m.upper(), p)
        for p, ops in app.openapi()["paths"].items()
        if "/gsc/" in p
        for m in ops
    ]
    assert len(paths) >= 25
    async with client(app, headers={}) as api:
        for method, path in paths:
            response = await api.request(method, path.replace("{", "").replace("}", ""))
            assert response.status_code == 401, (method, path, response.status_code)
        # The callback is the one public route (Google redirects the browser, no bearer possible).
        callback = await api.get(f"{BASE}/oauth/callback")
        assert (
            callback.status_code == 400
            and "text/html" in callback.headers["content-type"]
        )
    assert not any(
        "publish" in path or "wordpress" in path for _, path in paths
    )  # nothing can publish


async def test_status_without_credentials_is_a_disconnected_state_not_an_error(
    db_session, google
) -> None:
    async with client(make_app(db_session, google_client_id=None)) as api:
        body = (await api.get(f"{BASE}/status")).json()
        assert (
            body["state"] == "not_configured"
            and body["missing_settings"][0] == "GOOGLE_CLIENT_ID"
        )
        assert (await api.post(f"{BASE}/oauth/start")).status_code == 503
    async with client(make_app(db_session)) as api:
        body = (await api.get(f"{BASE}/status")).json()
        assert body["state"] == "disconnected" and body["scope"].endswith(
            "webmasters.readonly"
        )
        assert (
            body["scheduler_enabled"] is False
            and "not exact rank tracking" in body["position_note"]
        )
    assert google.token_forms == []


# -- the public callback ---------------------------------------------------------------------------------


async def test_callback_connects_with_a_valid_state_and_rejects_replays(
    db_session, google
) -> None:
    async with client(make_app(db_session)) as api:
        start = (await api.post(f"{BASE}/oauth/start")).json()
        assert start["expires_in_seconds"] == 600
        state = parse_qs(urlsplit(start["authorization_url"]).query)["state"][0]
        ok = await api.get(
            f"{BASE}/oauth/callback",
            params={"code": "c", "state": state},
            headers={"Authorization": ""},
        )
        assert ok.status_code == 200 and "Search Console connected" in ok.text
        assert (
            ok.headers["cache-control"] == "no-store"
            and ok.headers["referrer-policy"] == "no-referrer"
        )
        assert (await api.get(f"{BASE}/status")).json()["state"] == "connected"
        replay = await api.get(
            f"{BASE}/oauth/callback",
            params={"code": "c", "state": state},
            headers={"Authorization": ""},
        )
        assert replay.status_code == 400 and "already used" in replay.text
        forged = await api.get(
            f"{BASE}/oauth/callback",
            params={"code": "c", "state": "forged"},
            headers={"Authorization": ""},
        )
        assert forged.status_code == 400
        denied = await api.get(
            f"{BASE}/oauth/callback",
            params={"error": "access_denied"},
            headers={"Authorization": ""},
        )
        assert denied.status_code == 400 and "cancelled" in denied.text


async def test_callback_never_reflects_request_data(db_session, google) -> None:
    payload = "<script>alert(1)</script>"
    async with client(make_app(db_session), headers={}) as api:
        for params in (
            {"error": payload},
            {"code": payload, "state": payload},
            {"state": payload},
        ):
            response = await api.get(f"{BASE}/oauth/callback", params=params)
            assert response.status_code == 400
            assert "<script>" not in response.text and "alert(1)" not in response.text
    assert google.token_forms == []  # a forged state never reaches Google


async def test_callback_is_rate_limited(db_session, google) -> None:
    app = make_app(db_session)
    app.state.rate_limits = RateLimits.from_settings(
        Settings(api_public_rate_limit_per_minute=3)
    )
    async with client(app, headers={}) as api:
        codes = [
            (await api.get(f"{BASE}/oauth/callback", params={"state": "x"})).status_code
            for _ in range(5)
        ]
    assert codes[:3] == [400, 400, 400] and codes[3:] == [429, 429]


async def test_exchange_failure_is_shown_safely_and_connects_nothing(
    db_session, google
) -> None:
    google.code_scope = (
        "https://www.googleapis.com/auth/webmasters"  # broader than requested
    )
    async with client(make_app(db_session)) as api:
        state = parse_qs(
            urlsplit(
                (await api.post(f"{BASE}/oauth/start")).json()["authorization_url"]
            ).query
        )["state"][0]
        response = await api.get(
            f"{BASE}/oauth/callback",
            params={"code": "c", "state": state},
            headers={"Authorization": ""},
        )
        assert (
            response.status_code == 400
            and "read-only Search Console scope" in response.text
        )
        assert (await api.get(f"{BASE}/status")).json()["state"] == "disconnected"


# -- a complete journey ----------------------------------------------------------------------------------------------


async def test_connect_select_sync_read_and_map_through_the_api(
    db_session, google
) -> None:
    ds = [(END - timedelta(days=i)).isoformat() for i in range(3)]
    google.daily_rows = [daily(d) for d in ds]
    google.detail_rows = [
        row(ds[i % 3], f"query {i}", f"{SITE}/page-{i % 4}", clicks=2, impressions=20)
        for i in range(30)
    ]
    responses: list[str] = []

    async with client(make_app(db_session)) as api:

        async def call(method: str, path: str, **kw):
            response = await api.request(method, f"{BASE}{path}", **kw)
            responses.append(response.text)
            return response

        await authorize(api)
        sites = (await call("GET", "/properties")).json()
        assert [(s["site_url"], s["verified"]) for s in sites] == [
            ("https://www.example.com/", False),
            (PROPERTY, True),
        ]
        assert (
            await call(
                "PUT", "/property", json={"site_url": "https://www.example.com/"}
            )
        ).status_code == 422
        selected = (await call("PUT", "/property", json={"site_url": PROPERTY})).json()
        assert selected["selected_property"] == PROPERTY

        plan = (await call("POST", "/syncs", json={"max_requests": 4})).json()
        assert (
            plan["status"] == "planned" and google.query_bodies == []
        )  # planning makes no Google call
        assert (
            await call(
                "POST",
                f"/syncs/{plan['id']}/run",
                json={"approve": False, "max_requests": 3},
            )
        ).status_code == 422
        assert (
            await call(
                "POST",
                f"/syncs/{plan['id']}/run",
                json={"approve": True, "max_requests": 5},
            )
        ).status_code == 422
        ran = (
            await call(
                "POST",
                f"/syncs/{plan['id']}/run",
                json={"approve": True, "max_requests": 4},
            )
        ).json()
        assert ran["status"] == "completed" and ran["usage"]["rows_inserted"] == 30
        assert (
            await call(
                "POST",
                f"/syncs/{plan['id']}/run",
                json={"approve": True, "max_requests": 4},
            )
        ).status_code == 409
        assert [s["id"] for s in (await call("GET", "/syncs")).json()] == [plan["id"]]

        overview = (await call("GET", "/performance/overview")).json()
        assert overview["has_data"] and overview["totals"]["clicks"] == 30
        pages = (await call("GET", "/performance/pages", params={"limit": 2})).json()
        assert (
            len(pages["items"]) == 2
            and pages["items"][0]["clicks"] >= pages["items"][1]["clicks"]
        )
        queries = (await call("GET", "/performance/queries")).json()
        assert len(queries["items"]) == 30
        page = pages["items"][0]["page"]
        detail = (await call("GET", "/page", params={"url": page})).json()
        assert (
            detail["has_data"]
            and detail["queries"]
            and "not exact rank tracking" in detail["position_note"]
        )

        mapped = await call(
            "POST",
            "/mappings",
            json={
                "page_url": f"{SITE}/new-post",
                "keyword": "new post",
                "article_id": str(uuid4()),
                "published_at": (utc_now() - timedelta(days=1)).isoformat(),
            },
        )
        assert mapped.status_code == 201
        assert (
            await call(
                "POST", "/mappings", json={"page_url": "https://elsewhere.test/x"}
            )
        ).status_code == 422
        listed = (await call("GET", "/mappings")).json()
        assert [m["keyword"] for m in listed] == ["new post"]
        new = (await call("GET", "/page", params={"url": f"{SITE}/new-post"})).json()
        assert new["has_data"] is False and new["state"]["state"] == "too_new"
        toggled = (
            await call("PATCH", f"/mappings/{listed[0]['id']}", json={"active": False})
        ).json()
        assert (
            toggled["active"] is False and (await call("GET", "/mappings")).json() == []
        )

    # No secret ever left the API, whatever the endpoint.
    for text in responses:
        assert (
            "NEW-REFRESH-TOKEN" not in text
            and CLIENT_SECRET not in text
            and "access-token-" not in text
        )


# -- analysis, insights, refresh tasks and schedules ---------------------------------------------------------------------


async def test_analysis_insights_and_refresh_tasks_through_the_api(
    db_session, google
) -> None:
    app = make_app(db_session)
    async with client(app) as api:
        await authorize(api)
        await api.put(f"{BASE}/property", json={"site_url": PROPERTY})
        empty = (await api.post(f"{BASE}/analysis", json={})).json()
        assert empty["status"] == "no_data" and any(
            "Run a sync" in n for n in empty["notes"]
        )

        await seed_two_windows(SimpleNamespace(repository=GscRepository(db_session)))
        run = (
            await api.post(f"{BASE}/analysis", json={"thresholds": {"window_days": 28}})
        ).json()
        assert run["status"] == "completed" and run["window"]["days"] == 28
        insights = (await api.get(f"{BASE}/analysis/{run['id']}/insights")).json()
        kinds = {i["kind"] for i in insights}
        assert {"low_ctr", "declining_clicks", "new_query", "cannibalization"} <= kinds
        assert all(
            i["evidence"]
            and i["verification"]
            and i["impact"] in ("high", "medium", "low")
            for i in insights
        )
        only = (
            await api.get(
                f"{BASE}/analysis/{run['id']}/insights", params={"kind": "low_ctr"}
            )
        ).json()
        assert {i["kind"] for i in only} == {"low_ctr"}
        assert (await api.get(f"{BASE}/analysis")).json()[0]["id"] == run["id"]

        decline = next(i for i in insights if i["kind"] == "declining_clicks")
        task = await api.post(f"{BASE}/insights/{decline['id']}/refresh-task")
        assert task.status_code == 201 and task.json()["requires_new_approval"] is True
        assert task.json()[
            "status"
        ] == "proposed" and "Do not modify the published page" in " ".join(
            task.json()["brief"]["constraints"]
        )
        cannibal = next(i for i in insights if i["kind"] == "cannibalization")
        assert (
            await api.post(f"{BASE}/insights/{cannibal['id']}/refresh-task")
        ).status_code == 409
        task_id = task.json()["id"]
        assert (
            await api.patch(
                f"{BASE}/refresh-tasks/{task_id}", json={"status": "draft_ready"}
            )
        ).status_code == 409
        assert (
            await api.patch(
                f"{BASE}/refresh-tasks/{task_id}",
                json={"status": "accepted", "note": "ok"},
            )
        ).json()["status"] == "accepted"
        assert (
            await api.patch(
                f"{BASE}/refresh-tasks/{task_id}", json={"status": "published"}
            )
        ).status_code == 422
        assert [t["id"] for t in (await api.get(f"{BASE}/refresh-tasks")).json()] == [
            task_id
        ]
        dismissed = await api.patch(
            f"{BASE}/insights/{only[0]['id']}", json={"status": "dismissed"}
        )
        assert dismissed.json()["status"] == "dismissed"
        assert (await api.get(f"{BASE}/analysis/{uuid4()}/insights")).status_code == 404


async def test_schedules_through_the_api_start_disabled_and_need_their_own_approval(
    db_session, google
) -> None:
    async with client(make_app(db_session)) as api:
        await authorize(api)
        await api.put(f"{BASE}/property", json={"site_url": PROPERTY})
        created = await api.post(
            f"{BASE}/schedules",
            json={
                "name": "weekly sync",
                "kind": "sync",
                "interval_hours": 24,
                "lookback_days": 7,
                "budget_requests": 3,
            },
        )
        assert created.status_code == 201
        schedule = created.json()
        assert schedule["enabled"] is False and schedule["approved_at"] is None
        assert (
            await api.post(
                f"{BASE}/schedules/{schedule['id']}/enabled", json={"enabled": True}
            )
        ).status_code == 409
        assert (
            await api.post(
                f"{BASE}/schedules/{schedule['id']}/approve",
                json={"approve": True, "budget_requests": 9},
            )
        ).status_code == 422
        approved = await api.post(
            f"{BASE}/schedules/{schedule['id']}/approve",
            json={"approve": True, "approved_by": "me", "budget_requests": 3},
        )
        assert approved.json()["approved_by"] == "me"
        enabled = await api.post(
            f"{BASE}/schedules/{schedule['id']}/enabled", json={"enabled": True}
        )
        assert enabled.json()["enabled"] is True
        assert (await api.get(f"{BASE}/status")).json()[
            "scheduler_enabled"
        ] is False  # global switch still off
        assert (
            await api.post(
                f"{BASE}/schedules",
                json={
                    "name": "x",
                    "kind": "sync",
                    "interval_hours": 1,
                    "budget_requests": 3,
                },
            )
        ).status_code == 422
    assert (
        google.query_bodies == []
    )  # creating and approving schedules never calls Google


async def test_validation_and_unknown_ids(db_session, google) -> None:
    async with client(make_app(db_session)) as api:
        missing = str(uuid4())
        assert (await api.get(f"{BASE}/syncs/{missing}")).status_code == 404
        assert (await api.get(f"{BASE}/analysis/{missing}")).status_code == 404
        assert (
            await api.patch(f"{BASE}/mappings/{missing}", json={"active": True})
        ).status_code == 404
        assert (
            await api.patch(
                f"{BASE}/refresh-tasks/{missing}", json={"status": "accepted"}
            )
        ).status_code == 404
        assert (
            await api.post(f"{BASE}/insights/{missing}/refresh-task")
        ).status_code == 404
        assert (
            await api.post(f"{BASE}/disconnect", json={"revoke": False, "x": 1})
        ).status_code == 422
        assert (
            await api.post(f"{BASE}/syncs", json={"max_requests": 1})
        ).status_code == 422
        assert (
            await api.get(f"{BASE}/page", params={"url": "short"})
        ).status_code == 422
        assert (
            await api.post(f"{BASE}/analysis", json={"thresholds": {"window_days": 3}})
        ).status_code == 422
        # nothing connected: planning needs a selected property first
        assert (await api.post(f"{BASE}/syncs", json={})).status_code == 422


# -- the scheduler is opt-in ------------------------------------------------------------------------------


class _SpyScheduler:
    started = 0
    stopped = 0

    def __init__(self, *args, **kwargs) -> None:
        type(self).created = getattr(type(self), "created", 0) + 1

    async def run_forever(self, stop) -> None:
        type(self).started += 1
        await stop.wait()
        type(self).stopped += 1


def test_the_scheduler_is_not_started_by_default(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    _SpyScheduler.created = _SpyScheduler.started = _SpyScheduler.stopped = 0
    monkeypatch.setattr("app.main.GscScheduler", _SpyScheduler)
    with TestClient(create_app()) as app_client:
        assert app_client.get("/api/v1/health").status_code == 200
    assert (_SpyScheduler.created, _SpyScheduler.started) == (0, 0)


def test_the_scheduler_starts_only_when_enabled_and_stops_with_the_app(
    monkeypatch,
) -> None:
    from fastapi.testclient import TestClient

    from app.core.config import get_settings

    _SpyScheduler.created = _SpyScheduler.started = _SpyScheduler.stopped = 0
    monkeypatch.setenv("GSC_SCHEDULER_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("app.main.GscScheduler", _SpyScheduler)
    with TestClient(create_app()) as app_client:
        assert app_client.get("/api/v1/health").status_code == 200
    assert (_SpyScheduler.created, _SpyScheduler.started, _SpyScheduler.stopped) == (
        1,
        1,
        1,
    )
