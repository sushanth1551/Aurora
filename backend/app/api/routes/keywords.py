"""Keyword research endpoints (Phase 3), mounted under ``/seo/keywords`` (API token required).

``POST /runs`` plans (no SerpApi request); ``POST /runs/{id}/run`` needs an
explicit approval and runs synchronously within the approved search budget.
Selecting an opportunity only records the choice for the article phase; it
generates nothing.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import KeywordServiceDep
from app.core.exceptions import ResourceNotFoundException
from app.schemas.keywords import (
    CandidateList,
    CandidateView,
    CompetitorList,
    GapList,
    OpportunityList,
    OpportunityView,
    RunApproval,
    RunCreateRequest,
    RunList,
    RunView,
    SelectedList,
    SelectedOpportunity,
    SelectionRequest,
    SerpSnapshotView,
)
from app.seo.keywords.analysis import compare_competitors
from app.seo.keywords.models import Competitor

router = APIRouter()

_ERRORS: dict[int | str, dict[str, str]] = {
    404: {"description": "Run, opportunity or snapshot not found"},
    409: {"description": "Run not in a state that allows this action"},
    503: {"description": "SerpApi or the database is not configured/unavailable"},
}
Action = Literal[
    "write_new", "improve_existing", "monitor", "low_evidence", "deprioritized"
]
GAP_NOTES = {
    "ok": "Phrases and questions seen in competitor results that the site's own titles and headings lack.",
    "site_content_unavailable": (
        "No stored technical audit of this site was available, so the site's content was not read. "
        "Gaps cannot be claimed; run a technical SEO audit first."
    ),
}


@router.post(
    "/runs",
    response_model=RunView,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Plan a keyword research run (no SerpApi request)",
)
async def plan_run(body: RunCreateRequest, service: KeywordServiceDep) -> RunView:
    record = await service.plan(
        body.site_url,
        seeds=body.seeds,
        country=body.country.lower() if body.country else None,
        language=body.language,
        device=body.device,
        location=body.location,
        max_queries=body.max_queries,
        include_expansions=body.include_expansions,
    )
    return RunView.model_validate(record)


@router.get("/runs", response_model=RunList, summary="List runs (newest first)")
async def list_runs(
    service: KeywordServiceDep,
    host: Annotated[str | None, Query(max_length=253)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunList:
    rows, total = await service.repository.list_runs(
        host=host.lower() if host else None, limit=limit, offset=offset
    )
    return RunList(items=[RunView.model_validate(r) for r in rows], total=total)


@router.get(
    "/runs/{run_id}",
    response_model=RunView,
    responses=_ERRORS,
    summary="Run status and summary",
)
async def get_run(run_id: UUID, service: KeywordServiceDep) -> RunView:
    return RunView.model_validate(await service.get(run_id))


@router.post(
    "/runs/{run_id}/run",
    response_model=RunView,
    responses=_ERRORS,
    summary="Approve the search ceiling and run (synchronous)",
)
async def start_run(
    run_id: UUID, body: RunApproval, service: KeywordServiceDep
) -> RunView:
    return RunView.model_validate(
        await service.run(run_id, max_searches=body.max_searches)
    )


@router.get(
    "/runs/{run_id}/opportunities",
    response_model=OpportunityList,
    responses=_ERRORS,
    summary="Ranked content opportunities with SERP evidence",
)
async def list_opportunities(
    run_id: UUID,
    service: KeywordServiceDep,
    action: Action | None = None,
    selected: bool | None = None,
) -> OpportunityList:
    await service.get(run_id)
    rows = await service.repository.opportunities(
        run_id, action=action, selected=selected
    )
    return OpportunityList(
        items=[OpportunityView.model_validate(r) for r in rows], total=len(rows)
    )


@router.get(
    "/runs/{run_id}/candidates",
    response_model=CandidateList,
    responses=_ERRORS,
    summary="All keywords considered, with source, intent and cluster",
)
async def list_candidates(
    run_id: UUID,
    service: KeywordServiceDep,
    intent: Literal["informational", "commercial", "transactional", "navigational"]
    | None = None,
    cluster: Annotated[str | None, Query(max_length=300)] = None,
    searched: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CandidateList:
    await service.get(run_id)
    rows, total = await service.repository.candidates(
        run_id,
        intent=intent,
        cluster=cluster,
        searched=searched,
        limit=limit,
        offset=offset,
    )
    return CandidateList(
        items=[CandidateView.model_validate(r) for r in rows], total=total
    )


@router.get(
    "/runs/{run_id}/competitors",
    response_model=CompetitorList,
    responses=_ERRORS,
    summary="SERP competitors observed for the researched queries",
)
async def list_competitors(run_id: UUID, service: KeywordServiceDep) -> CompetitorList:
    return CompetitorList(items=list((await service.get(run_id)).competitors))


@router.get(
    "/runs/{run_id}/competitors/compare",
    responses=_ERRORS,
    summary="Compare chosen competitors by the keywords they rank for",
)
async def compare(
    run_id: UUID,
    service: KeywordServiceDep,
    domains: Annotated[list[str], Query(min_length=2, max_length=10)],
) -> dict[str, object]:
    run = await service.get(run_id)
    competitors = [Competitor.model_validate(c) for c in run.competitors]
    return compare_competitors(competitors, [d[:253] for d in domains])


@router.get(
    "/runs/{run_id}/gaps",
    response_model=GapList,
    responses=_ERRORS,
    summary="Content gaps with verbatim competitor evidence",
)
async def list_gaps(run_id: UUID, service: KeywordServiceDep) -> GapList:
    run = await service.get(run_id)
    gap_status = run.gap_status or "not_computed"
    return GapList(
        status=gap_status,
        note=GAP_NOTES.get(gap_status, "Run has not finished."),
        items=list(run.gaps),
    )


@router.get(
    "/serp/{snapshot_id}",
    response_model=SerpSnapshotView,
    responses=_ERRORS,
    summary="One stored SERP observation (organic results and features)",
)
async def get_serp(snapshot_id: UUID, service: KeywordServiceDep) -> SerpSnapshotView:
    record = await service.repository.get_snapshot(snapshot_id)
    if record is None:
        raise ResourceNotFoundException("SERP snapshot not found.")
    return SerpSnapshotView.model_validate(record)


@router.post(
    "/opportunities/{opportunity_id}/selection",
    response_model=OpportunityView,
    responses=_ERRORS,
    summary="Select or unselect an opportunity for article generation",
)
async def select_opportunity(
    opportunity_id: UUID, body: SelectionRequest, service: KeywordServiceDep
) -> OpportunityView:
    record = await service.repository.set_selected(opportunity_id, body.selected)
    if record is None:
        raise ResourceNotFoundException("Opportunity not found.")
    return OpportunityView.model_validate(record)


@router.get(
    "/selected",
    response_model=SelectedList,
    summary="Opportunities selected for article generation",
)
async def selected(
    service: KeywordServiceDep,
    host: Annotated[str | None, Query(max_length=253)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> SelectedList:
    rows = await service.repository.selected_opportunities(
        host.lower() if host else None, limit
    )
    return SelectedList(
        items=[
            SelectedOpportunity(
                opportunity_id=o.id,
                run_id=o.run_id,
                host=run_host,
                keyword=o.keyword,
                intent=o.intent,
                action=o.action,
                cluster=o.cluster,
                gap_terms=o.gap_terms,
                questions=o.questions,
                serp_snapshot_id=(o.serp or {}).get("snapshot_id"),
                selected_at=o.selected_at,
                run_created_at=created,
            )
            for o, run_host, created in rows
        ]
    )
