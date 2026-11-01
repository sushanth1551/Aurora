"""Search Console endpoints (Phase 6), mounted under ``/gsc``.

Everything except the OAuth callback needs the API token. The callback is public
because Google redirects the *browser* to it (no bearer header is possible); it is
protected by the single-use ``state`` issued by ``POST /oauth/start``, by a rate
limit, and it renders a fixed page that never echoes request data.

Reads of stored data make no Google call. Calls to Google happen only for:
``/connection/verify``, ``/disconnect?revoke``, ``/properties``, ``PUT /property``
and ``POST /syncs/{id}/run`` (which needs an explicit approval).
"""

import html
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import HTMLResponse

from app.api.deps import GscServiceDep
from app.api.security import limit_public_requests
from app.core.exceptions import AuroraException, ResourceNotFoundException
from app.schemas.gsc import (
    AnalysisRequest,
    AnalysisView,
    DisconnectRequest,
    InsightStatus,
    InsightView,
    MappingActive,
    MappingRequest,
    MappingView,
    PropertySelection,
    ScheduleApproval,
    ScheduleCreate,
    ScheduleEnabled,
    ScheduleView,
    SiteView,
    SyncApproval,
    SyncPlanRequest,
    SyncView,
    TaskUpdate,
    TaskView,
)
from app.tools.errors import ProviderError

router = APIRouter()
public_router = APIRouter()

_ERRORS: dict[int | str, dict[str, str]] = {
    404: {"description": "Not found"},
    409: {"description": "Not connected, re-authorization needed, or invalid state"},
    503: {"description": "OAuth client not configured or database unavailable"},
}
PageSize = Annotated[int, Query(ge=1, le=200)]


# -- OAuth callback (public) ---------------------------------------------------------------


def _page(title: str, message: str, ok: bool) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        f"<title>{html.escape(title)}</title></head>"
        '<body style="font-family:system-ui,sans-serif;max-width:34rem;margin:4rem auto;padding:0 1rem">'
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>"
        + ("<p>You can close this tab and return to AURORA.</p>" if ok else "")
        + "</body></html>"
    )


@public_router.get(
    "/oauth/callback",
    response_class=HTMLResponse,
    include_in_schema=False,
    dependencies=[Depends(limit_public_requests)],
)
async def oauth_callback(
    service: GscServiceDep,
    response: Response,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    state: Annotated[str | None, Query(max_length=256)] = None,
    error: Annotated[str | None, Query(max_length=128)] = None,
) -> HTMLResponse:
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    try:
        await service.complete_authorization(code=code, state=state, error=error)
    except (AuroraException, ProviderError) as exc:
        return HTMLResponse(
            _page("Search Console was not connected", exc.message, ok=False),
            status_code=400
            if isinstance(exc, AuroraException) and exc.status_code < 500
            else 502,
            headers=headers,
        )
    return HTMLResponse(
        _page(
            "Search Console connected",
            "AURORA can now read your Search Console data (read-only).",
            ok=True,
        ),
        headers=headers,
    )


# -- connection ------------------------------------------------------------------------------


@router.get("/status", summary="Connection state (no Google call)")
async def get_status(service: GscServiceDep) -> dict[str, Any]:
    return await service.status()


@router.post(
    "/oauth/start",
    responses=_ERRORS,
    summary="Create the Google consent URL (read-only scope)",
)
async def oauth_start(service: GscServiceDep) -> dict[str, Any]:
    return await service.begin_authorization()


@router.post(
    "/connection/verify",
    responses=_ERRORS,
    summary="Refresh the token with Google (one call)",
)
async def verify_connection(service: GscServiceDep) -> dict[str, Any]:
    return await service.verify_connection()


@router.post("/disconnect", responses=_ERRORS, summary="Remove the stored credential")
async def disconnect(body: DisconnectRequest, service: GscServiceDep) -> dict[str, Any]:
    return await service.disconnect(revoke=body.revoke)


@router.get(
    "/properties",
    response_model=list[SiteView],
    responses=_ERRORS,
    summary="Properties the account can access (one Google call)",
)
async def properties(service: GscServiceDep) -> list[SiteView]:
    return [SiteView(**s.model_dump()) for s in await service.list_properties()]


@router.put(
    "/property",
    responses=_ERRORS,
    summary="Select a verified property (one Google call)",
)
async def select_property(
    body: PropertySelection, service: GscServiceDep
) -> dict[str, Any]:
    return await service.select_property(body.site_url)


# -- sync -------------------------------------------------------------------------------------


@router.post(
    "/syncs",
    response_model=SyncView,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Plan a data fetch (no Google call)",
)
async def plan_sync(body: SyncPlanRequest, service: GscServiceDep) -> SyncView:
    record = await service.plan_sync(
        property_id=body.property,
        start=body.start_date,
        end=body.end_date,
        max_requests=body.max_requests,
    )
    return SyncView.model_validate(record)


@router.get("/syncs", response_model=list[SyncView], summary="Recent syncs")
async def list_syncs(
    service: GscServiceDep,
    property: Annotated[str | None, Query(max_length=512)] = None,
    limit: PageSize = 20,
) -> list[SyncView]:
    return [
        SyncView.model_validate(r)
        for r in await service.repository.list_syncs(property, limit)
    ]


@router.get(
    "/syncs/{sync_id}", response_model=SyncView, responses=_ERRORS, summary="One sync"
)
async def get_sync(sync_id: UUID, service: GscServiceDep) -> SyncView:
    return SyncView.model_validate(await service.get_sync(sync_id))


@router.post(
    "/syncs/{sync_id}/run",
    response_model=SyncView,
    responses=_ERRORS,
    summary="Approve the request ceiling and fetch (synchronous)",
)
async def run_sync(
    sync_id: UUID, body: SyncApproval, service: GscServiceDep
) -> SyncView:
    return SyncView.model_validate(
        await service.run_sync(sync_id, max_requests=body.max_requests)
    )


# -- stored performance (no Google call) -----------------------------------------------------


@router.get(
    "/performance/overview",
    responses=_ERRORS,
    summary="Daily totals and totals for a range",
)
async def overview(
    service: GscServiceDep,
    property: Annotated[str | None, Query(max_length=512)] = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    return await service.overview(property, start, end)


@router.get("/performance/{by}", responses=_ERRORS, summary="Top pages or queries")
async def top(
    by: Literal["pages", "queries", "page-queries"],
    service: GscServiceDep,
    property: Annotated[str | None, Query(max_length=512)] = None,
    start: date | None = None,
    end: date | None = None,
    limit: PageSize = 50,
) -> dict[str, Any]:
    key = {"pages": "page", "queries": "query", "page-queries": "page_query"}[by]
    return await service.top(property, key, start, end, limit)


@router.get("/page", responses=_ERRORS, summary="Series and queries for one page")
async def page_detail(
    url: Annotated[str, Query(min_length=8, max_length=2048)],
    service: GscServiceDep,
    property: Annotated[str | None, Query(max_length=512)] = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    return await service.page_detail(property, url, start, end)


# -- article mapping --------------------------------------------------------------------------


@router.post(
    "/mappings",
    response_model=MappingView,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Map a page URL (and keyword) to an article",
)
async def add_mapping(body: MappingRequest, service: GscServiceDep) -> MappingView:
    record = await service.add_mapping(
        property_id=body.property,
        page_url=body.page_url,
        keyword=body.keyword,
        article_id=body.article_id,
        article_version_id=body.article_version_id,
        publication_id=body.publication_id,
        published_at=body.published_at,
    )
    return MappingView.model_validate(record)


@router.get(
    "/mappings",
    response_model=list[MappingView],
    responses=_ERRORS,
    summary="Active mappings",
)
async def list_mappings(
    service: GscServiceDep,
    property: Annotated[str | None, Query(max_length=512)] = None,
    include_inactive: bool = False,
) -> list[MappingView]:
    prop = await service.property_for(property)
    rows = await service.repository.mappings(prop, active_only=not include_inactive)
    return [MappingView.model_validate(r) for r in rows]


@router.patch(
    "/mappings/{mapping_id}",
    response_model=MappingView,
    responses=_ERRORS,
    summary="Deactivate or reactivate a mapping (nothing is deleted)",
)
async def set_mapping_active(
    mapping_id: UUID, body: MappingActive, service: GscServiceDep
) -> MappingView:
    record = await service.repository.set_mapping_active(mapping_id, body.active)
    if record is None:
        raise ResourceNotFoundException("Mapping not found.")
    return MappingView.model_validate(record)


# -- analysis, insights and refresh tasks -------------------------------------------------------


@router.post(
    "/analysis",
    response_model=AnalysisView,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Analyze stored data (no Google call)",
)
async def analyze(body: AnalysisRequest, service: GscServiceDep) -> AnalysisView:
    return AnalysisView.model_validate(
        await service.analyze(property_id=body.property, thresholds=body.thresholds)
    )


@router.get("/analysis", response_model=list[AnalysisView], summary="Recent analyses")
async def list_analyses(
    service: GscServiceDep,
    property: Annotated[str | None, Query(max_length=512)] = None,
    limit: PageSize = 20,
) -> list[AnalysisView]:
    return [
        AnalysisView.model_validate(r)
        for r in await service.repository.list_analyses(property, limit)
    ]


@router.get(
    "/analysis/{analysis_id}",
    response_model=AnalysisView,
    responses=_ERRORS,
    summary="One analysis",
)
async def get_analysis(analysis_id: UUID, service: GscServiceDep) -> AnalysisView:
    record = await service.repository.get_analysis(analysis_id)
    if record is None:
        raise ResourceNotFoundException("Analysis not found.")
    return AnalysisView.model_validate(record)


@router.get(
    "/analysis/{analysis_id}/insights",
    response_model=list[InsightView],
    responses=_ERRORS,
    summary="Findings and recommendations, ranked",
)
async def list_insights(
    analysis_id: UUID,
    service: GscServiceDep,
    kind: Annotated[str | None, Query(max_length=32)] = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=16)] = None,
    tier: Annotated[str | None, Query(max_length=24)] = None,
) -> list[InsightView]:
    if await service.repository.get_analysis(analysis_id) is None:
        raise ResourceNotFoundException("Analysis not found.")
    rows = await service.repository.insights(
        analysis_id, kind=kind, status=status_filter, tier=tier
    )
    return [InsightView.model_validate(r) for r in rows]


@router.patch(
    "/insights/{insight_id}",
    response_model=InsightView,
    responses=_ERRORS,
    summary="Accept or dismiss a finding",
)
async def set_insight_status(
    insight_id: UUID, body: InsightStatus, service: GscServiceDep
) -> InsightView:
    return InsightView.model_validate(
        await service.set_insight_status(insight_id, body.status)
    )


@router.post(
    "/insights/{insight_id}/refresh-task",
    response_model=TaskView,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Create a refresh brief for a draft (never edits content)",
)
async def create_refresh_task(insight_id: UUID, service: GscServiceDep) -> TaskView:
    return TaskView.model_validate(await service.create_refresh_task(insight_id))


@router.get("/refresh-tasks", response_model=list[TaskView], summary="Refresh tasks")
async def list_tasks(
    service: GscServiceDep,
    property: Annotated[str | None, Query(max_length=512)] = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=24)] = None,
) -> list[TaskView]:
    return [
        TaskView.model_validate(r)
        for r in await service.repository.tasks(property, status_filter)
    ]


@router.patch(
    "/refresh-tasks/{task_id}",
    response_model=TaskView,
    responses=_ERRORS,
    summary="Move a refresh task along its workflow",
)
async def update_task(
    task_id: UUID, body: TaskUpdate, service: GscServiceDep
) -> TaskView:
    return TaskView.model_validate(
        await service.update_refresh_task(task_id, status=body.status, note=body.note)
    )


# -- schedules (disabled by default) -------------------------------------------------------------


@router.get("/schedules", response_model=list[ScheduleView], summary="Schedules")
async def list_schedules(service: GscServiceDep) -> list[ScheduleView]:
    return [
        ScheduleView.model_validate(r) for r in await service.repository.schedules()
    ]


@router.post(
    "/schedules",
    response_model=ScheduleView,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Create a schedule (disabled and unapproved)",
)
async def create_schedule(body: ScheduleCreate, service: GscServiceDep) -> ScheduleView:
    record = await service.create_schedule(
        name=body.name,
        property_id=body.property,
        kind=body.kind,
        interval_hours=body.interval_hours,
        lookback_days=body.lookback_days,
        budget_requests=body.budget_requests,
    )
    return ScheduleView.model_validate(record)


@router.post(
    "/schedules/{schedule_id}/approve",
    response_model=ScheduleView,
    responses=_ERRORS,
    summary="Approve this schedule's own request ceiling",
)
async def approve_schedule(
    schedule_id: UUID, body: ScheduleApproval, service: GscServiceDep
) -> ScheduleView:
    record = await service.approve_schedule(
        schedule_id, approved_by=body.approved_by, budget_requests=body.budget_requests
    )
    return ScheduleView.model_validate(record)


@router.post(
    "/schedules/{schedule_id}/enabled",
    response_model=ScheduleView,
    responses=_ERRORS,
    summary="Enable or disable an approved schedule",
)
async def set_schedule_enabled(
    schedule_id: UUID, body: ScheduleEnabled, service: GscServiceDep
) -> ScheduleView:
    return ScheduleView.model_validate(
        await service.set_schedule_enabled(schedule_id, body.enabled)
    )
