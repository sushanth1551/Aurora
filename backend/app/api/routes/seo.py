"""Technical SEO audit endpoints (Phase 2), mounted under ``/seo`` (API token required).

``POST /audits`` plans (no network request); ``POST /audits/{id}/run`` needs an
explicit approval and runs synchronously within the approved budget.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import PlainTextResponse

from app.api.deps import SeoAuditServiceDep
from app.core.exceptions import ResourceNotFoundException
from app.schemas.seo_audit import (
    AuditCreateRequest,
    AuditList,
    AuditRunRequest,
    AuditView,
    FindingList,
    FindingView,
    PageDetail,
    PageList,
    PageSummary,
    RecommendationList,
    RecommendationView,
)
from app.seo.technical.report import build_report, render_markdown

router = APIRouter()

_ERRORS: dict[int | str, dict[str, str]] = {
    404: {"description": "Audit or page not found"},
    409: {"description": "Audit not in a state that allows this action"},
    503: {"description": "Firecrawl or the database is not configured/unavailable"},
}
Severity = Literal["high", "medium", "low", "info"]
Tier = Literal["blocker", "cheap_certainty", "high_leverage", "maintenance", "deferred"]


@router.post(
    "/audits",
    response_model=AuditView,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Plan a technical SEO audit (no network request)",
)
async def plan_audit(
    body: AuditCreateRequest, service: SeoAuditServiceDep
) -> AuditView:
    record = await service.plan(
        body.url,
        max_pages=body.max_pages,
        max_link_checks=body.max_link_checks,
        max_sitemaps=body.max_sitemaps,
    )
    return AuditView.model_validate(record)


@router.get("/audits", response_model=AuditList, summary="List audits (newest first)")
async def list_audits(
    service: SeoAuditServiceDep,
    host: Annotated[str | None, Query(max_length=253)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditList:
    rows, total = await service.repository.list_audits(
        host=host.lower() if host else None, limit=limit, offset=offset
    )
    return AuditList(items=[AuditView.model_validate(r) for r in rows], total=total)


@router.get(
    "/audits/{audit_id}",
    response_model=AuditView,
    responses=_ERRORS,
    summary="Audit status and summary",
)
async def get_audit(audit_id: UUID, service: SeoAuditServiceDep) -> AuditView:
    return AuditView.model_validate(await service.get(audit_id))


@router.post(
    "/audits/{audit_id}/run",
    response_model=AuditView,
    responses=_ERRORS,
    summary="Approve the estimate and run the audit (synchronous)",
)
async def run_audit(
    audit_id: UUID, body: AuditRunRequest, service: SeoAuditServiceDep
) -> AuditView:
    record = await service.run(
        audit_id, max_firecrawl_scrapes=body.max_firecrawl_scrapes
    )
    return AuditView.model_validate(record)


@router.get(
    "/audits/{audit_id}/pages",
    response_model=PageList,
    responses=_ERRORS,
    summary="Audited pages",
)
async def list_pages(
    audit_id: UUID,
    service: SeoAuditServiceDep,
    fetch_status: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageList:
    await service.get(audit_id)
    rows, total = await service.repository.pages(
        audit_id, limit=limit, offset=offset, fetch_status=fetch_status
    )
    items = []
    for row in rows:
        titles = (row.signals or {}).get("titles") or []
        items.append(
            PageSummary(
                id=row.id,
                url=row.url,
                final_url=row.final_url,
                status_code=row.status_code,
                status_source=row.status_source,
                fetch_status=row.fetch_status,
                fetch_source=row.fetch_source,
                depth=row.depth,
                title=titles[0] if titles else None,
                indexability=(row.indexability or {}).get("state", "unknown"),
                indexability_reasons=(row.indexability or {}).get("reasons", []),
                googlebot_allowed=row.googlebot_allowed,
            )
        )
    return PageList(items=items, total=total)


@router.get(
    "/audits/{audit_id}/pages/{page_id}",
    response_model=PageDetail,
    responses=_ERRORS,
    summary="All signals observed for one page",
)
async def get_page(
    audit_id: UUID, page_id: UUID, service: SeoAuditServiceDep
) -> PageDetail:
    row = await service.repository.page(audit_id, page_id)
    if row is None:
        raise ResourceNotFoundException("Page not found in this audit.")
    return PageDetail.model_validate(row)


@router.get(
    "/audits/{audit_id}/findings",
    response_model=FindingList,
    responses=_ERRORS,
    summary="Findings with affected URLs and evidence",
)
async def list_findings(
    audit_id: UUID,
    service: SeoAuditServiceDep,
    severity: Severity | None = None,
    category: Annotated[str | None, Query(max_length=32)] = None,
) -> FindingList:
    await service.get(audit_id)
    rows = await service.repository.findings(
        audit_id, severity=severity, category=category
    )
    return FindingList(
        items=[FindingView.model_validate(r) for r in rows], total=len(rows)
    )


@router.get(
    "/audits/{audit_id}/recommendations",
    response_model=RecommendationList,
    responses=_ERRORS,
    summary="Prioritized recommendations",
)
async def list_recommendations(
    audit_id: UUID, service: SeoAuditServiceDep, tier: Tier | None = None
) -> RecommendationList:
    await service.get(audit_id)
    rows = await service.repository.recommendations(audit_id, tier=tier)
    return RecommendationList(
        items=[RecommendationView.model_validate(r) for r in rows], total=len(rows)
    )


@router.get(
    "/audits/{audit_id}/report",
    responses=_ERRORS,
    summary="Audit report (JSON or Markdown)",
)
async def get_report(
    audit_id: UUID,
    service: SeoAuditServiceDep,
    format: Literal["json", "markdown"] = "json",
):  # noqa: A002 - query name
    audit = await service.get(audit_id)
    findings = await service.repository.findings(audit_id)
    recommendations = await service.repository.recommendations(audit_id)
    report = build_report(audit, findings, recommendations)
    if format == "markdown":
        return PlainTextResponse(
            render_markdown(report), media_type="text/markdown; charset=utf-8"
        )
    return report
