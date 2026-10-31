"""Knowledge graph endpoints (Module 5), mounted under ``/graph``.

Read endpoints are bounded (``GRAPH_MAX_QUERY_RESULTS``, ``GRAPH_MAX_TRAVERSAL_DEPTH``)
and accept only allowlisted labels and relationship types; there is no
endpoint that runs caller-supplied Cypher. Without Neo4j configured, graph
reads return ``503 graph_not_configured``; ``POST /sync`` with ``dry_run`` still
works because planning only reads PostgreSQL.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import GraphClientDep, GraphQueryDep, GraphSyncDep, GraphSyncRepoDep
from app.graph.graph_health import GraphHealth, check_health
from app.graph.graph_sync_service import ReconcileReport
from app.graph.models import SyncRun
from app.schemas.graph import (
    GraphDocument,
    ItemsResponse,
    OutboxView,
    ProcessRequest,
    ReconcileRequest,
    SyncRequest,
    SyncRunsResponse,
    SyncStatusResponse,
)

router = APIRouter()

_ERRORS: dict[int | str, dict[str, str]] = {
    404: {"description": "Entity or research job not found"},
    422: {"description": "Invalid label, relationship type, depth or limit"},
    503: {"description": "Neo4j not configured/unavailable, or sync disabled"},
}
Limit = Annotated[
    int | None, Query(ge=1, le=1000, description="Capped by GRAPH_MAX_QUERY_RESULTS")
]
Offset = Annotated[int, Query(ge=0, le=100_000)]


def _items(
    items: list, limit: int | None = None, offset: int = 0, note: str | None = None
) -> ItemsResponse:
    return ItemsResponse(
        items=items, count=len(items), limit=limit, offset=offset, note=note
    )


# -- health and sync --------------------------------------------------------------------


@router.get(
    "/health",
    response_model=GraphHealth,
    summary="Neo4j connectivity (verified round trip)",
)
async def graph_health(client: GraphClientDep) -> GraphHealth:
    return await check_health(client)


@router.post(
    "/sync",
    response_model=SyncRun,
    responses=_ERRORS,
    summary="Project one research job into the graph",
)
async def sync_job(request: SyncRequest, service: GraphSyncDep) -> SyncRun:
    return await service.sync_job(request.research_job_id, dry_run=request.dry_run)


@router.post(
    "/sync/reconcile",
    response_model=ReconcileReport,
    responses=_ERRORS,
    summary="Compare PostgreSQL with the graph",
)
async def reconcile(
    request: ReconcileRequest, service: GraphSyncDep
) -> ReconcileReport:
    return await service.reconcile(dry_run=request.dry_run)


@router.post(
    "/sync/process",
    response_model=SyncRunsResponse,
    responses=_ERRORS,
    summary="Process pending and due failed events",
)
async def process_due(
    request: ProcessRequest, service: GraphSyncDep
) -> SyncRunsResponse:
    return SyncRunsResponse(runs=await service.process_due(limit=request.limit))


@router.post(
    "/sync/retry",
    response_model=SyncRunsResponse,
    responses=_ERRORS,
    summary="Retry failed events that are due",
)
async def retry_failed(
    request: ProcessRequest, service: GraphSyncDep
) -> SyncRunsResponse:
    return SyncRunsResponse(
        runs=await service.process_due(limit=request.limit, include_pending=False)
    )


@router.get(
    "/sync/status/{job_id}",
    response_model=SyncStatusResponse,
    responses=_ERRORS,
    summary="Synchronization status of a job",
)
async def sync_status(job_id: UUID, repo: GraphSyncRepoDep) -> SyncStatusResponse:
    event = await repo.get_event(job_id)
    runs = await repo.runs_for_job(job_id)
    return SyncStatusResponse(
        research_job_id=job_id,
        synchronized=bool(event and event.status == "succeeded"),
        outbox=OutboxView(
            status=event.status,
            attempt_count=event.attempt_count,
            next_retry_at=event.next_retry_at,
            last_error=event.last_error,
            processed_at=event.processed_at,
            updated_at=event.updated_at,
        )
        if event
        else None,  # fmt: skip
        recent_runs=[
            {
                "run_id": str(r.id),
                "mode": r.mode,
                "status": r.status,
                "dry_run": r.dry_run,
                "counts": r.counts,
                "error": r.error_summary,
                "started_at": r.started_at,
                "duration_ms": r.duration_ms,
            }
            for r in runs
        ],  # fmt: skip
    )


@router.get(
    "/review",
    response_model=ItemsResponse,
    summary="Entity matches kept separate for human review",
)
async def review_candidates(
    repo: GraphSyncRepoDep, limit: Limit = 100
) -> ItemsResponse:
    items = [
        r.model_dump(mode="json") for r in await repo.review_candidates(limit or 100)
    ]
    return _items(items, limit)


# -- entities ---------------------------------------------------------------------------------


@router.get(
    "/entities",
    response_model=ItemsResponse,
    responses=_ERRORS,
    summary="Find entities by label and name",
)
async def find_entities(
    service: GraphQueryDep,
    label: Annotated[
        str, Query(description="Company, Product, Service, Technology, ...")
    ],
    name: Annotated[str | None, Query(max_length=200)] = None,
    domain: Annotated[str | None, Query(max_length=253)] = None,
    limit: Limit = None,
) -> ItemsResponse:
    return _items(
        await service.search(label, name=name, domain=domain, limit=limit), limit
    )


@router.get("/entities/{entity_id}", response_model=GraphDocument, responses=_ERRORS)
async def get_entity(entity_id: str, service: GraphQueryDep) -> GraphDocument:
    return await service.entity(entity_id)


@router.get(
    "/entities/{entity_id}/relationships",
    response_model=ItemsResponse,
    responses=_ERRORS,
)
async def entity_relationships(
    entity_id: str,
    service: GraphQueryDep,
    direction: Literal["out", "in", "both"] = "both",
    types: Annotated[
        list[str] | None, Query(description="Relationship types (allowlist)")
    ] = None,
    labels: Annotated[
        list[str] | None, Query(description="Neighbour labels (allowlist)")
    ] = None,
    limit: Limit = None,
    offset: Offset = 0,
) -> ItemsResponse:
    items = await service.relationships(
        entity_id,
        direction=direction,
        types=types,
        labels=labels,
        limit=limit,
        offset=offset,
    )
    return _items(items, limit, offset)


@router.get(
    "/entities/{entity_id}/evidence", response_model=GraphDocument, responses=_ERRORS
)
async def entity_evidence(entity_id: str, service: GraphQueryDep) -> GraphDocument:
    return await service.evidence(entity_id)


@router.get(
    "/entities/{entity_id}/claims", response_model=ItemsResponse, responses=_ERRORS
)
async def entity_claims(
    entity_id: str, service: GraphQueryDep, limit: Limit = None
) -> ItemsResponse:
    return _items(await service.entity_claims(entity_id, limit=limit), limit)


@router.get(
    "/explore",
    response_model=GraphDocument,
    responses=_ERRORS,
    summary="Bounded neighbourhood",
)
async def explore(
    service: GraphQueryDep,
    entity_id: str,
    depth: Annotated[int, Query(ge=1, le=5)] = 1,
    types: Annotated[list[str] | None, Query()] = None,
    limit: Limit = None,
) -> GraphDocument:
    return await service.explore(entity_id, depth=depth, types=types, limit=limit)


# -- companies / products / other ---------------------------------------------------------------


@router.get("/companies/{company_id}", response_model=GraphDocument, responses=_ERRORS)
async def company_profile(company_id: str, service: GraphQueryDep) -> GraphDocument:
    return await service.company_profile(company_id)


@router.get(
    "/companies/{company_id}/products", response_model=ItemsResponse, responses=_ERRORS
)
async def company_products(
    company_id: str, service: GraphQueryDep, limit: Limit = None
) -> ItemsResponse:
    return _items(await service.company_products(company_id, limit), limit)


@router.get(
    "/companies/{company_id}/services", response_model=ItemsResponse, responses=_ERRORS
)
async def company_services(
    company_id: str, service: GraphQueryDep, limit: Limit = None
) -> ItemsResponse:
    return _items(await service.company_services(company_id, limit), limit)


@router.get(
    "/companies/{company_id}/competitors",
    response_model=ItemsResponse,
    responses=_ERRORS,
)
async def company_competitors(
    company_id: str, service: GraphQueryDep, limit: Limit = None
) -> ItemsResponse:
    return _items(
        await service.competitors(company_id, limit), limit,
        note="assertion=inferred means a documented rule (e.g. same industry), not documented competition",
    )  # fmt: skip


@router.get(
    "/companies/{company_id}/network", response_model=GraphDocument, responses=_ERRORS
)
async def competitor_network(company_id: str, service: GraphQueryDep, depth: Annotated[int, Query(ge=1, le=5)] = 2,
                             limit: Limit = None) -> GraphDocument:  # fmt: skip
    return await service.competitor_network(company_id, depth=depth, limit=limit)


@router.get(
    "/companies/{company_a}/compare/{company_b}",
    response_model=GraphDocument,
    responses=_ERRORS,
)
async def compare_companies(
    company_a: str, company_b: str, service: GraphQueryDep
) -> GraphDocument:
    return await service.compare_companies(company_a, company_b)


@router.get(
    "/products/{product_id}/technologies",
    response_model=ItemsResponse,
    responses=_ERRORS,
)
async def product_technologies(
    product_id: str, service: GraphQueryDep, limit: Limit = None
) -> ItemsResponse:
    return _items(await service.product_technologies(product_id, limit), limit,
                  note="technology edges built from page mentions are inferred, not proof of use")  # fmt: skip


@router.get(
    "/products/{product_id}/features", response_model=ItemsResponse, responses=_ERRORS
)
async def product_features(
    product_id: str, service: GraphQueryDep, limit: Limit = None
) -> ItemsResponse:
    return _items(await service.product_features(product_id, limit), limit)


@router.get(
    "/products/{product_id}/integrations",
    response_model=ItemsResponse,
    responses=_ERRORS,
)
async def product_integrations(
    product_id: str, service: GraphQueryDep, limit: Limit = None
) -> ItemsResponse:
    return _items(await service.product_integrations(product_id, limit), limit)


@router.get(
    "/products/{product_id}/competitors",
    response_model=ItemsResponse,
    responses=_ERRORS,
)
async def product_competitors(
    product_id: str, service: GraphQueryDep, limit: Limit = None
) -> ItemsResponse:
    return _items(await service.competitors(product_id, limit), limit)


@router.get(
    "/products/{product_a}/compare/{product_b}",
    response_model=GraphDocument,
    responses=_ERRORS,
)
async def compare_products(
    product_a: str, product_b: str, service: GraphQueryDep
) -> GraphDocument:
    return await service.compare_products(product_a, product_b)


@router.get(
    "/technologies/{technology_id}/companies",
    response_model=ItemsResponse,
    responses=_ERRORS,
)
async def technology_companies(
    technology_id: str, service: GraphQueryDep, limit: Limit = None
) -> ItemsResponse:
    return _items(await service.technology_companies(technology_id, limit), limit)


@router.get(
    "/technologies/{technology_id}/network",
    response_model=GraphDocument,
    responses=_ERRORS,
)
async def technology_network(technology_id: str, service: GraphQueryDep, depth: Annotated[int, Query(ge=1, le=5)] = 2,
                             limit: Limit = None) -> GraphDocument:  # fmt: skip
    return await service.technology_network(technology_id, depth=depth, limit=limit)


@router.get(
    "/features/{feature_id}/products", response_model=ItemsResponse, responses=_ERRORS
)
async def feature_products(
    feature_id: str, service: GraphQueryDep, limit: Limit = None
) -> ItemsResponse:
    return _items(await service.feature_products(feature_id, limit), limit)


@router.get(
    "/articles/{article_id}/topics", response_model=ItemsResponse, responses=_ERRORS
)
async def article_topics(
    article_id: str, service: GraphQueryDep, limit: Limit = None
) -> ItemsResponse:
    return _items(await service.article_topics(article_id, limit), limit)


@router.get(
    "/topics/{topic_id}/articles", response_model=ItemsResponse, responses=_ERRORS
)
async def topic_articles(
    topic_id: str, service: GraphQueryDep, limit: Limit = None
) -> ItemsResponse:
    return _items(await service.topic_articles(topic_id, limit), limit)


@router.get(
    "/claims/{claim_id}/sources", response_model=ItemsResponse, responses=_ERRORS
)
async def claim_sources(claim_id: str, service: GraphQueryDep) -> ItemsResponse:
    return _items(await service.claim_sources(claim_id))
