"""Search Console service on SQLite with a scripted Google (offline)."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
import respx
from sqlalchemy import select

from app.core.exceptions import ConflictException, ResearchException
from app.database.models import GscConnectionRecord
from app.database.repositories.gsc_repository import GscRepository
from app.monitoring.errors import (
    GscNotConnected,
    GscOAuthError,
    GscReauthorizationRequired,
    GscStorageUnavailable,
)
from app.monitoring.models import Thresholds
from app.monitoring.oauth import SCOPE, OAuthFlows
from app.monitoring.scheduler import GscScheduler
from app.monitoring.service import GscService, RefreshTokenSource
from app.schemas.research import utc_now
from app.tools.search_console_tool import SearchConsoleTool
from tests.gsc_support import (
    END,
    PROPERTY,
    REFRESH_TOKEN,
    SITE,
    TODAY,
    FakeVault,
    daily,
    install,
    row,
    seed_two_windows,
    settings,
)


@pytest.fixture
def google():
    with respx.mock(assert_all_called=False) as mock:
        yield install(mock)


async def _no_sleep(_: float) -> None:
    return None


def make_service(db_session, *, vault=None, env_token=None, **overrides) -> GscService:
    vault = FakeVault() if vault is None else vault
    cfg = settings(**overrides)
    if env_token:
        from pydantic import SecretStr

        cfg = cfg.model_copy(update={"google_refresh_token": SecretStr(env_token)})
    repo = GscRepository(db_session)
    tokens = RefreshTokenSource(repo, vault, cfg)
    tool = SearchConsoleTool(cfg, refresh_token_provider=tokens.get, sleep=_no_sleep)
    return GscService(
        repo=repo,
        settings=cfg,
        tool=tool,
        tokens=tokens,
        vault=vault,
        flows=OAuthFlows(600),
        today=lambda: TODAY,
    )


async def connect(service: GscService, *, prop: str | None = PROPERTY) -> None:
    await service.repository.save_connection(
        state="connected",
        sealed=FakeVault().seal(REFRESH_TOKEN),
        vault="fake",
        scope=SCOPE,
    )
    if prop:
        await service.repository.set_property(prop)


def state_of(url: str) -> str:
    return parse_qs(urlsplit(url).query)["state"][0]


# -- connection status ---------------------------------------------------------------------------


async def test_status_reports_each_connection_state_without_calling_google(
    db_session, google
) -> None:
    unconfigured = make_service(db_session, google_client_id=None)
    assert (await unconfigured.status())["state"] == "not_configured"
    assert (await unconfigured.status())["missing_settings"][0] == "GOOGLE_CLIENT_ID"

    service = make_service(db_session)
    status = await service.status()
    assert status["state"] == "disconnected" and status["token_source"] == "none"
    assert (
        status["scope"] == SCOPE
        and "not exact rank tracking" in status["position_note"]
    )
    assert status["scheduler_enabled"] is False

    await connect(service)
    status = await service.status()
    assert status["state"] == "connected" and status["token_source"] == "stored"
    assert status["selected_property"] == PROPERTY and status["verified"] is False

    await service.repository.set_connection_state(
        "reauthorization_required", error="invalid_grant"
    )
    assert (await service.status())["state"] == "reauthorization_required"
    assert (
        google.token_forms == [] and google.auth_headers == []
    )  # no Google call at all


async def test_environment_refresh_token_counts_as_connected(
    db_session, google
) -> None:
    service = make_service(db_session, vault=None, env_token="1//ENV-TOKEN")
    service._vault = None  # no secure storage on this machine
    status = await service.status()
    assert status["state"] == "connected" and status["token_source"] == "environment"
    assert (
        status["can_store_token"] is False
        and status["token_storage"] == "environment_only"
    )


# -- authorization ------------------------------------------------------------------------------


async def test_authorization_stores_only_a_sealed_token(db_session, google) -> None:
    service = make_service(db_session)
    started = await service.begin_authorization()
    assert parse_qs(urlsplit(started["authorization_url"]).query)["scope"] == [SCOPE]
    await service.complete_authorization(
        code="the-code", state=state_of(started["authorization_url"]), error=None
    )

    record = (await db_session.execute(select(GscConnectionRecord))).scalar_one()
    assert (
        record.state == "connected" and record.vault == "fake" and record.scope == SCOPE
    )
    assert (
        record.refresh_token_sealed
        and "NEW-REFRESH-TOKEN" not in record.refresh_token_sealed
    )
    assert (
        await RefreshTokenSource(
            service.repository, FakeVault(), service._settings
        ).get()
        == "1//NEW-REFRESH-TOKEN-456"
    )
    assert (await service.status())["state"] == "connected"


async def test_authorization_state_is_single_use_and_user_denial_is_handled(
    db_session, google
) -> None:
    service = make_service(db_session)
    state = state_of((await service.begin_authorization())["authorization_url"])
    await service.complete_authorization(code="c", state=state, error=None)
    with pytest.raises(GscOAuthError, match="invalid, expired or was already used"):
        await service.complete_authorization(
            code="c", state=state, error=None
        )  # replay
    with pytest.raises(GscOAuthError, match="invalid, expired"):
        await service.complete_authorization(code="c", state="forged", error=None)
    with pytest.raises(GscOAuthError, match="cancelled"):
        await service.complete_authorization(
            code=None, state=None, error="access_denied"
        )
    with pytest.raises(GscOAuthError, match="incomplete"):
        await service.complete_authorization(code=None, state=None, error=None)


async def test_authorization_needs_a_secure_place_to_store_the_token(
    db_session, google
) -> None:
    service = make_service(db_session, vault=None)
    service._vault = None
    with pytest.raises(GscStorageUnavailable, match="GOOGLE_REFRESH_TOKEN"):
        await service.begin_authorization()


async def test_oauth_start_requires_a_configured_client(db_session, google) -> None:
    from app.core.exceptions import ConfigurationException

    with pytest.raises(ConfigurationException, match="GOOGLE_CLIENT_ID"):
        await make_service(db_session, google_client_id=None).begin_authorization()


# -- verification, properties, disconnect ------------------------------------------------------------


async def test_verify_connection_records_success_and_reauthorization(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    status = await service.verify_connection()
    assert status["state"] == "connected" and status["verified"] is True
    google.token_error = "invalid_grant"
    service._tool.forget_access_token()
    with pytest.raises(GscReauthorizationRequired):
        await service.verify_connection()
    assert (await service.status())["state"] == "reauthorization_required"


async def test_verify_without_a_token_is_not_connected(db_session, google) -> None:
    with pytest.raises(GscNotConnected):
        await make_service(db_session).verify_connection()


async def test_only_verified_properties_can_be_selected(db_session, google) -> None:
    service = make_service(db_session)
    await connect(service, prop=None)
    listed = {s.site_url: s.verified for s in await service.list_properties()}
    assert listed == {PROPERTY: True, "https://www.example.com/": False}
    assert (await service.select_property(PROPERTY))["selected_property"] == PROPERTY
    with pytest.raises(ResearchException, match="not available"):
        await service.select_property("https://www.example.com/")  # unverified
    with pytest.raises(ResearchException, match="not available"):
        await service.select_property("sc-domain:someone-else.com")
    with pytest.raises(Exception, match="property"):
        await service.select_property("not a property")
    assert (await service.status())["selected_property"] == PROPERTY


async def test_disconnect_removes_the_stored_token_and_optionally_revokes(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    google.mock.post("https://oauth2.googleapis.com/revoke").respond(200)
    status = await service.disconnect(revoke=True)
    assert status["state"] == "disconnected" and status["token_source"] == "none"
    record = (await db_session.execute(select(GscConnectionRecord))).scalar_one()
    assert (
        record.refresh_token_sealed is None and record.selected_property == PROPERTY
    )  # data stays


# -- sync -----------------------------------------------------------------------------------------------


def dataset(google, n_rows: int) -> None:
    ds = [(END - timedelta(days=i)).isoformat() for i in range(3)]
    google.daily_rows = [daily(d, clicks=10, impressions=100) for d in ds]
    google.detail_rows = [
        row(
            ds[i % 3],
            f"query {i}",
            f"{SITE}/page-{i % 7}",
            clicks=1,
            impressions=10,
            position=4.0,
        )
        for i in range(n_rows)
    ]


async def test_sync_plan_makes_no_google_call_and_enforces_caps(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    plan = await service.plan_sync()
    assert (
        plan.status == "planned"
        and plan.end_date == END
        and (plan.end_date - plan.start_date).days == 27
    )
    assert plan.limits == {"max_requests": 6, "rows_per_request": 100}
    assert google.token_forms == [] and google.query_bodies == []
    with pytest.raises(ResearchException, match="exceed the configured maximum"):
        await service.plan_sync(max_requests=7)
    with pytest.raises(ResearchException, match="older than"):
        await service.plan_sync(start=TODAY - timedelta(days=900), end=END)
    with pytest.raises(ResearchException, match="future"):
        await service.plan_sync(start=END, end=TODAY + timedelta(days=1))
    with pytest.raises(ResearchException, match="after the end"):
        await service.plan_sync(start=END, end=END - timedelta(days=2))


async def test_sync_requires_a_selected_property(db_session, google) -> None:
    service = make_service(db_session)
    await connect(service, prop=None)
    with pytest.raises(ResearchException, match="Select a Search Console property"):
        await service.plan_sync()


async def test_sync_stores_rows_pages_through_results_and_is_idempotent(
    db_session, google
) -> None:
    dataset(google, 150)  # 100 + 50 rows: two detail pages
    service = make_service(db_session)
    await connect(service)
    plan = await service.plan_sync()
    done = await service.run_sync(plan.id, max_requests=6)

    assert done.status == "completed" and done.error_summary is None
    assert done.usage["requests"] == 3 and done.usage["rows_inserted"] == 150
    assert done.usage["daily_inserted"] == 3 and done.usage["stopped_reason"] is None
    assert [b["startRow"] for b in google.query_bodies] == [0, 0, 100]
    assert all(b["dataState"] == "final" for b in google.query_bodies)

    again = await service.run_sync((await service.plan_sync()).id, max_requests=6)
    assert (
        again.usage["rows_inserted"] == 0 and again.usage["rows_skipped"] == 150
    )  # nothing duplicated
    overview = await service.overview(None, None, None)
    assert (
        overview["totals"]["clicks"] == 30 and overview["days_with_data"] == 3
    )  # totals not doubled


async def test_sync_stops_at_the_approved_request_ceiling(db_session, google) -> None:
    dataset(google, 250)  # would need 1 + 3 requests
    service = make_service(db_session)
    await connect(service)
    plan = await service.plan_sync()
    done = await service.run_sync(plan.id, max_requests=2)
    assert done.status == "partially_completed" and done.usage["requests"] == 2
    assert (
        done.usage["stopped_reason"] == "request_budget"
        and done.usage["rows_inserted"] == 100
    )
    assert len(google.query_bodies) == 2  # never exceeded the ceiling


async def test_sync_approval_cannot_exceed_the_plan_and_needs_two_requests(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    plan = await service.plan_sync(max_requests=3)
    with pytest.raises(ResearchException, match="exceeds the plan"):
        await service.run_sync(plan.id, max_requests=4)
    with pytest.raises(ResearchException, match="at least 2"):
        await service.run_sync(plan.id, max_requests=1)
    assert (
        google.token_forms == []
        and (await service.get_sync(plan.id)).status == "planned"
    )


async def test_sync_refuses_when_not_connected_before_marking_anything(
    db_session, google
) -> None:
    service = make_service(db_session)
    await service.repository.set_property(PROPERTY)
    plan = await service.plan_sync()
    with pytest.raises(GscNotConnected):
        await service.run_sync(plan.id, max_requests=3)
    assert (
        await service.get_sync(plan.id)
    ).status == "planned" and google.token_forms == []


async def test_a_planned_sync_runs_once_and_not_alongside_another(
    db_session, google
) -> None:
    dataset(google, 5)
    service = make_service(db_session)
    await connect(service)
    plan = await service.plan_sync()
    await service.run_sync(plan.id, max_requests=3)
    with pytest.raises(ConflictException, match="only a planned sync"):
        await service.run_sync(plan.id, max_requests=3)
    running = await service.plan_sync()
    other = await service.plan_sync()
    await service.repository.mark_sync_running(running.id, {"x": 1})
    with pytest.raises(ConflictException, match="already running"):
        await service.run_sync(other.id, max_requests=3)


async def test_revoked_authorization_during_sync_fails_cleanly_and_flags_reauthorization(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    google.token_error = "invalid_grant"
    done = await service.run_sync((await service.plan_sync()).id, max_requests=3)
    assert done.status == "failed" and "no longer accepts" in done.error_summary
    assert (await service.status())["state"] == "reauthorization_required"


async def test_provider_error_midway_keeps_what_was_stored(db_session, google) -> None:
    dataset(google, 250)
    service = make_service(db_session)
    await connect(service)
    google.query_statuses = [
        200,
        200,
        403,
    ]  # totals + first page succeed, second page denied
    done = await service.run_sync((await service.plan_sync()).id, max_requests=6)
    assert done.status == "partially_completed" and done.usage["rows_inserted"] == 100
    assert "provider_authentication_error" in done.error_summary


async def test_an_empty_range_stores_nothing_and_invents_nothing(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    done = await service.run_sync((await service.plan_sync()).id, max_requests=3)
    assert (
        done.status == "completed"
        and done.usage["rows_fetched"] == 0
        and done.usage["rows_inserted"] == 0
    )
    overview = await service.overview(None, None, None)
    assert (
        overview["has_data"] is False
        and overview["series"] == []
        and overview["totals"] is None
    )
    assert "Run a sync" in overview["note"]


# -- reading stored data -------------------------------------------------------------------------------


async def test_reads_come_only_from_storage_and_show_overview_totals(
    db_session, google
) -> None:
    dataset(google, 12)
    service = make_service(db_session)
    await connect(service)
    await service.run_sync((await service.plan_sync()).id, max_requests=3)
    calls_before = len(google.auth_headers)

    overview = await service.overview(None, None, None)
    assert overview["has_data"] and len(overview["series"]) == 3
    assert overview["totals"]["clicks"] == 30 and overview["totals"][
        "ctr"
    ] == pytest.approx(0.1)
    assert overview["totals"]["position"] == pytest.approx(3.0)
    pages = await service.top(None, "page", None, None, 5)
    assert pages["items"][0]["page"].startswith(SITE) and len(pages["items"]) == 5
    queries = await service.top(None, "query", None, None, 100)
    assert len(queries["items"]) == 12
    page = pages["items"][0]["page"]
    detail = await service.page_detail(None, page, None, None)
    assert detail["has_data"] and detail["queries"] and detail["series"]
    assert (
        len(google.auth_headers) == calls_before
    )  # reading storage never calls Google


async def test_a_new_article_without_data_is_explained_not_zeroed(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    published = utc_now() - timedelta(days=2)
    page = f"{SITE}/blog/brand-new"
    await service.add_mapping(
        property_id=None,
        page_url=page,
        keyword="brand new",
        article_id=uuid4(),
        article_version_id=None,
        publication_id=None,
        published_at=published,
    )
    detail = await service.page_detail(None, page, None, None)
    assert detail["has_data"] is False and detail["series"] == []
    assert (
        detail["state"]["state"] == "too_new"
        and "expected, not a problem" in detail["state"]["note"]
    )


# -- mappings ------------------------------------------------------------------------------------------------


async def test_mappings_validate_the_property_and_upsert(db_session, google) -> None:
    service = make_service(db_session)
    await connect(service)
    article = uuid4()
    first = await service.add_mapping(
        property_id=None,
        page_url=f"{SITE}/p/",
        keyword=" Email  Tips ",
        article_id=article,
        article_version_id=None,
        publication_id=None,
        published_at=None,
    )
    again = await service.add_mapping(
        property_id=None,
        page_url=f"{SITE}/p",
        keyword="email tips",
        article_id=article,
        article_version_id=uuid4(),
        publication_id=None,
        published_at=None,
    )
    assert first.id == again.id  # same page and keyword after normalization
    assert len(await service.repository.mappings(PROPERTY)) == 1
    with pytest.raises(ResearchException, match="does not belong"):
        await service.add_mapping(
            property_id=None,
            page_url="https://evil.test/p",
            keyword=None,
            article_id=None,
            article_version_id=None,
            publication_id=None,
            published_at=None,
        )
    with pytest.raises(ResearchException, match="does not belong"):
        await service.add_mapping(
            property_id=None,
            page_url="https://notexample.com/p",
            keyword=None,
            article_id=None,
            article_version_id=None,
            publication_id=None,
            published_at=None,
        )  # suffix trick must not match
    prefix = make_service(db_session)
    await prefix.repository.set_property("https://www.example.com/blog/")
    with pytest.raises(ResearchException, match="does not belong"):
        await prefix.add_mapping(
            property_id=None,
            page_url=f"{SITE}/other/x",
            keyword=None,
            article_id=None,
            article_version_id=None,
            publication_id=None,
            published_at=None,
        )
    hidden = await service.repository.set_mapping_active(first.id, False)
    assert hidden and not (await service.repository.mappings(PROPERTY))
    assert (
        len(await service.repository.mappings(PROPERTY, active_only=False)) == 1
    )  # nothing deleted


# -- analysis on a synthetic two-window dataset ---------------------------------------------------------------


async def test_analysis_finds_each_kind_with_evidence_and_never_calls_google(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    article = uuid4()
    await service.add_mapping(
        property_id=None,
        page_url=f"{SITE}/declining",
        keyword="declining query",
        article_id=article,
        article_version_id=None,
        publication_id=None,
        published_at=utc_now() - timedelta(days=90),
    )
    await seed_two_windows(service)
    run = await service.analyze(property_id=None, thresholds=Thresholds())
    insights = await service.repository.insights(run.id)
    by_kind = {}
    for i in insights:
        by_kind.setdefault(i.kind, []).append(i)

    assert run.status == "completed" and run.summary["comparison_possible"] is True
    assert {
        "low_ctr",
        "declining_clicks",
        "declining_position",
        "new_query",
        "cannibalization",
        "growing_page",
    } <= set(by_kind)
    low = by_kind["low_ctr"][0]
    assert low.subject == f"{SITE}/low-ctr" and low.evidence["query"] == "slow query"
    assert (
        low.evidence["baseline_ctr"] == pytest.approx(0.1, abs=0.01)
        and low.evidence["baseline_rows"] >= 30
    )
    decline = by_kind["declining_clicks"][0]
    assert (
        decline.subject == f"{SITE}/declining"
        and decline.evidence["driver"] == "ranking_loss"
    )
    assert (
        decline.evidence["clicks_previous"] == 560
        and decline.evidence["clicks_current"] == 224
    )
    assert (
        decline.article_id == article
        and decline.refresh_candidate is True
        and decline.basis == "measured"
    )
    assert by_kind["declining_position"][0].evidence[
        "position_change"
    ] == pytest.approx(3.5)
    new = by_kind["new_query"][0]
    assert (
        new.subject == "brand new query"
        and new.evidence["top_page"] == f"{SITE}/healthy-0"
    )
    cannibal = by_kind["cannibalization"][0]
    assert (
        cannibal.subject == "shared query"
        and cannibal.basis == "inferred"
        and cannibal.confidence == "low"
    )
    assert [p["page"] for p in cannibal.evidence["pages"]] == [f"{SITE}/a", f"{SITE}/b"]
    assert (
        by_kind["growing_page"][0].subject == f"{SITE}/growing"
        and by_kind["growing_page"][0].tier == "info"
    )
    assert [i.rank for i in insights] == sorted(i.rank for i in insights)
    for i in insights:
        assert i.evidence and i.action and i.verification and i.rationale
    assert (
        google.token_forms == [] and google.auth_headers == []
    )  # analysis uses stored data only


async def test_analysis_with_one_window_skips_comparisons_and_says_why(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    await seed_two_windows(service, previous=False)
    run = await service.analyze(property_id=None, thresholds=Thresholds())
    kinds = {i.kind for i in await service.repository.insights(run.id)}
    assert run.summary["comparison_possible"] is False
    assert (
        "declining_clicks" not in kinds
        and "growing_page" not in kinds
        and "declining_position" not in kinds
    )
    assert (
        "new_query" not in kinds
    )  # nothing earlier is stored, so nothing can be called new
    assert any("Not enough history" in n for n in run.notes) and any(
        "No data exists before" in n for n in run.notes
    )
    assert "low_ctr" in kinds  # single-window checks still run


async def test_analysis_without_any_data_reports_it_and_still_explains_new_articles(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    await service.add_mapping(
        property_id=None,
        page_url=f"{SITE}/new-post",
        keyword="new post",
        article_id=uuid4(),
        article_version_id=None,
        publication_id=None,
        published_at=utc_now() - timedelta(days=3),
    )
    run = await service.analyze(property_id=None, thresholds=Thresholds())
    insights = await service.repository.insights(run.id)
    assert run.status == "no_data" and any("Run a sync" in n for n in run.notes)
    assert [i.kind for i in insights] == ["no_data_yet"]
    assert insights[0].evidence["state"] == "too_new" and insights[0].tier == "info"
    assert "do not change the page" in insights[0].action.lower()


async def test_analysis_flags_a_mapped_page_that_has_no_rows_after_two_weeks(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    await seed_two_windows(service)
    await service.add_mapping(
        property_id=None,
        page_url=f"{SITE}/ghost",
        keyword=None,
        article_id=None,
        article_version_id=None,
        publication_id=None,
        published_at=utc_now() - timedelta(days=40),
    )
    run = await service.analyze(property_id=None, thresholds=Thresholds())
    ghost = next(
        i for i in await service.repository.insights(run.id) if i.kind == "no_data_yet"
    )
    assert (
        ghost.evidence["state"] == "no_data"
        and "cannot tell whether it is indexed" in ghost.rationale
    )


# -- insights and refresh tasks ----------------------------------------------------------------------------------


async def analysis_insights(service: GscService):
    await seed_two_windows(service)
    run = await service.analyze(property_id=None, thresholds=Thresholds())
    return {i.kind: i for i in await service.repository.insights(run.id)}


async def test_refresh_task_is_a_brief_for_a_new_draft_and_never_touches_content(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    insights = await analysis_insights(service)
    task = await service.create_refresh_task(insights["declining_clicks"].id)

    assert task.status == "proposed" and task.requires_new_approval is True
    assert task.page_url == f"{SITE}/declining" and task.property == PROPERTY
    constraints = " ".join(task.brief["constraints"])
    assert (
        "Do not modify the published page" in constraints
        and "new approval" in constraints.lower()
    )
    assert task.brief["page_top_queries"][0]["query"] == "declining query"
    assert (
        task.brief["proposals"] and task.brief["evidence"]["driver"] == "ranking_loss"
    )
    assert (
        await service.repository.get_insight(insights["declining_clicks"].id)
    ).status == "accepted"
    again = await service.create_refresh_task(insights["declining_clicks"].id)
    assert again.id == task.id  # idempotent: one task per finding


async def test_findings_that_need_investigation_or_no_action_cannot_become_drafts(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    insights = await analysis_insights(service)
    for kind in ("cannibalization", "growing_page"):
        with pytest.raises(
            ConflictException, match="does not call for a content refresh"
        ):
            await service.create_refresh_task(insights[kind].id)


async def test_refresh_task_workflow_transitions(db_session, google) -> None:
    service = make_service(db_session)
    await connect(service)
    insights = await analysis_insights(service)
    task = await service.create_refresh_task(insights["low_ctr"].id)
    with pytest.raises(ConflictException):
        await service.update_refresh_task(
            task.id, status="draft_ready", note=None
        )  # cannot skip ahead
    for status in ("accepted", "draft_requested", "draft_ready"):
        task = await service.update_refresh_task(
            task.id, status=status, note=f"to {status}"
        )
        assert (
            task.status == status and task.requires_new_approval is True
        )  # never cleared
    task = await service.update_refresh_task(task.id, status="dismissed", note=None)
    with pytest.raises(ConflictException):
        await service.update_refresh_task(
            task.id, status="accepted", note=None
        )  # dismissed is final


async def test_insight_status_changes_are_validated(db_session, google) -> None:
    service = make_service(db_session)
    await connect(service)
    insight = (await analysis_insights(service))["low_ctr"]
    assert (
        await service.set_insight_status(insight.id, "dismissed")
    ).status == "dismissed"
    with pytest.raises(ResearchException):
        await service.set_insight_status(insight.id, "published")


# -- schedules -------------------------------------------------------------------------------------------------------


def scheduler_for(
    db_session, service: GscService, *, enabled: bool, clock
) -> GscScheduler:

    cfg = service._settings.model_copy(update={"gsc_scheduler_enabled": enabled})

    class Factory:
        def __call__(self):
            class Ctx:
                async def __aenter__(self_inner):
                    return db_session

                async def __aexit__(self_inner, *args):
                    return False

            return Ctx()

    return GscScheduler(Factory(), cfg, lambda session: service, clock=clock)  # type: ignore[arg-type]


async def test_schedules_start_disabled_unapproved_and_need_their_own_ceiling(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    schedule = await service.create_schedule(
        name="weekly",
        property_id=None,
        kind="sync",
        interval_hours=24,
        lookback_days=7,
        budget_requests=3,
    )
    assert (
        schedule.enabled is False
        and schedule.approved_at is None
        and schedule.next_run_at is None
    )
    with pytest.raises(ConflictException, match="Approve"):
        await service.set_schedule_enabled(schedule.id, True)
    with pytest.raises(ResearchException, match="must equal"):
        await service.approve_schedule(schedule.id, approved_by="me", budget_requests=5)
    approved = await service.approve_schedule(
        schedule.id, approved_by="me", budget_requests=3
    )
    assert approved.approved_by == "me" and approved.approved_at
    enabled = await service.set_schedule_enabled(schedule.id, True)
    assert enabled.enabled and enabled.next_run_at > utc_now()
    for bad in (
        {"interval_hours": 1},
        {"budget_requests": 1},
        {"budget_requests": 99},
        {"kind": "delete"},
    ):
        args = {
            "name": "x",
            "property_id": None,
            "kind": "sync",
            "interval_hours": 24,
            "lookback_days": 7,
            "budget_requests": 3,
        } | bad
        with pytest.raises(ResearchException):
            await service.create_schedule(**args)


async def test_scheduler_does_nothing_when_disabled_even_for_due_approved_schedules(
    db_session, google
) -> None:
    dataset(google, 5)
    service = make_service(db_session)
    await connect(service)
    schedule = await service.create_schedule(
        name="s",
        property_id=None,
        kind="sync",
        interval_hours=6,
        lookback_days=3,
        budget_requests=3,
    )
    await service.approve_schedule(schedule.id, approved_by="me", budget_requests=3)
    await service.set_schedule_enabled(schedule.id, True)
    later = utc_now() + timedelta(days=2)
    assert (
        await scheduler_for(
            db_session, service, enabled=False, clock=lambda: later
        ).run_due()
        == []
    )
    assert google.token_forms == []


async def test_scheduler_runs_a_due_schedule_once_within_its_own_ceiling(
    db_session, google
) -> None:
    dataset(google, 250)
    service = make_service(db_session)
    await connect(service)
    schedule = await service.create_schedule(
        name="s",
        property_id=None,
        kind="sync",
        interval_hours=6,
        lookback_days=3,
        budget_requests=2,
    )
    await service.approve_schedule(schedule.id, approved_by="me", budget_requests=2)
    await service.set_schedule_enabled(schedule.id, True)
    later = utc_now() + timedelta(hours=7)
    scheduler = scheduler_for(db_session, service, enabled=True, clock=lambda: later)

    first = await scheduler.run_due()
    assert [r["status"] for r in first] == [
        "partially_completed"
    ]  # stopped at the schedule's ceiling
    assert len(google.query_bodies) == 2
    assert await scheduler.run_due() == []  # not due again until the interval passes
    record = await service.repository.get_schedule(schedule.id)
    assert record.last_status == "partially_completed" and record.next_run_at > later
    sync = (await service.repository.list_syncs(PROPERTY, 5))[0]
    assert sync.source == "schedule" and sync.approval["max_requests"] == 2


async def test_scheduler_skips_unapproved_or_disabled_schedules_and_records_failures(
    db_session, google
) -> None:
    service = make_service(db_session)
    await connect(service)
    unapproved = await service.create_schedule(
        name="u",
        property_id=None,
        kind="analyze",
        interval_hours=6,
        lookback_days=7,
        budget_requests=0,
    )
    unapproved.enabled, unapproved.next_run_at = (
        True,
        utc_now() - timedelta(hours=1),
    )  # forced, bypassing the API
    await service.repository.save(unapproved)
    later = utc_now() + timedelta(hours=1)
    assert (
        await scheduler_for(
            db_session, service, enabled=True, clock=lambda: later
        ).run_due()
        == []
    )

    broken = await service.create_schedule(
        name="b",
        property_id=None,
        kind="sync",
        interval_hours=6,
        lookback_days=3,
        budget_requests=3,
    )
    await service.approve_schedule(broken.id, approved_by="me", budget_requests=3)
    await service.set_schedule_enabled(broken.id, True)
    google.token_error = "invalid_grant"
    result = await scheduler_for(
        db_session, service, enabled=True, clock=lambda: utc_now() + timedelta(hours=7)
    ).run_due()
    assert result[0]["status"] == "failed"
    assert (await service.repository.get_schedule(broken.id)).last_status == "failed"
