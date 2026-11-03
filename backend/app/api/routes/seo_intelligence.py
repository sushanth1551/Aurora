"""SEO Intelligence endpoints, mounted under ``/seo/intelligence`` (API token required).

Read-mostly: websites, plans, findings, execution history and reports come from
the file-backed knowledge base. The only write imports a finished AURORA
technical audit into a website's knowledge (local files; no network request).
Skill-proposal decisions are deliberately CLI-only (``scripts/seo_intelligence.py``).
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Path, Query, status
from fastapi.responses import PlainTextResponse

from app.api.deps import SeoAgentServiceDep, SeoAuditServiceDep, SkillImprovementDep
from app.schemas.seo_intelligence import (
    AuditImportRequest,
    ExecutionList,
    ExecutionPlan,
    FindingList,
    ProposalList,
    SkillList,
    WebsiteContext,
    WebsiteList,
)
from app.seo.intelligence.adapters import import_technical_audit
from app.seo.intelligence.models import Execution, Priority, WebsiteConfig

router = APIRouter()

_ERRORS: dict[int | str, dict[str, str]] = {
    404: {"description": "Website, execution, mode or report not found"},
    409: {"description": "State does not allow this action"},
    503: {"description": "Knowledge base or skill registry is invalid"},
}
SiteId = Annotated[str, Path(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]
ExecutionId = Annotated[str, Path(pattern=r"^[a-z0-9-]+$", max_length=80)]
Mode = Literal[
    "audit",
    "competitor-analysis",
    "content-optimization",
    "performance-review",
    "update-skills",
]


@router.get("/websites", response_model=WebsiteList, summary="Configured websites")
async def list_websites(service: SeoAgentServiceDep) -> WebsiteList:
    items = service.websites()
    return WebsiteList(items=items, total=len(items))


@router.get(
    "/websites/{site_id}",
    response_model=WebsiteConfig,
    responses=_ERRORS,
    summary="One website's configuration",
)
async def get_website(site_id: SiteId, service: SeoAgentServiceDep) -> WebsiteConfig:
    return service.website(site_id)


@router.get(
    "/websites/{site_id}/context",
    response_model=WebsiteContext,
    responses=_ERRORS,
    summary="What the next execution should know (open findings, decisions, history)",
)
async def get_context(site_id: SiteId, service: SeoAgentServiceDep) -> WebsiteContext:
    return WebsiteContext(context=service.context(site_id))


@router.get(
    "/websites/{site_id}/plan",
    response_model=ExecutionPlan,
    responses=_ERRORS,
    summary="Ordered skill steps for an execution mode, with availability",
)
async def get_plan(
    site_id: SiteId, service: SeoAgentServiceDep, mode: Mode = "audit"
) -> ExecutionPlan:
    return ExecutionPlan(site_id=site_id, mode=mode, steps=service.plan(site_id, mode))


@router.get(
    "/websites/{site_id}/findings",
    response_model=FindingList,
    responses=_ERRORS,
    summary="Finding ledger (all executions), highest priority first",
)
async def list_findings(
    site_id: SiteId,
    service: SeoAgentServiceDep,
    status_filter: Annotated[str | None, Query(alias="status", max_length=24)] = None,
    priority: Priority | None = None,
    active_only: bool = False,
) -> FindingList:
    items = service.findings(
        site_id, status=status_filter, priority=priority, active_only=active_only
    )
    return FindingList(items=items, total=len(items))


@router.get(
    "/websites/{site_id}/executions",
    response_model=ExecutionList,
    responses=_ERRORS,
    summary="Execution history (newest first)",
)
async def list_executions(
    site_id: SiteId, service: SeoAgentServiceDep
) -> ExecutionList:
    items = service.executions(site_id)
    return ExecutionList(items=items, total=len(items))


@router.get(
    "/websites/{site_id}/executions/{execution_id}",
    response_model=Execution,
    responses=_ERRORS,
    summary="Full execution record",
)
async def get_execution(
    site_id: SiteId, execution_id: ExecutionId, service: SeoAgentServiceDep
) -> Execution:
    return service.execution(site_id, execution_id)


@router.get(
    "/websites/{site_id}/executions/{execution_id}/report",
    response_class=PlainTextResponse,
    responses=_ERRORS,
    summary="Execution report (Markdown)",
)
async def get_report(
    site_id: SiteId, execution_id: ExecutionId, service: SeoAgentServiceDep
) -> PlainTextResponse:
    return PlainTextResponse(
        service.report(site_id, execution_id), media_type="text/markdown"
    )


@router.post(
    "/websites/{site_id}/imports/technical-audits/{audit_id}",
    response_model=Execution,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
    summary="Import a finished AURORA technical audit as an execution",
)
async def import_audit(
    site_id: SiteId,
    audit_id: UUID,
    body: AuditImportRequest,
    service: SeoAgentServiceDep,
    audits: SeoAuditServiceDep,
) -> Execution:
    return await import_technical_audit(service, audits.repository, site_id, audit_id)


@router.get(
    "/skills", response_model=SkillList, responses=_ERRORS, summary="Skill registry"
)
async def list_skills(service: SeoAgentServiceDep) -> SkillList:
    items = service.registry.skills()
    return SkillList(items=items, total=len(items), problems=service.registry.check())


@router.get(
    "/skills/proposals",
    response_model=ProposalList,
    summary="Skill-improvement proposals (decisions are made in the CLI)",
)
async def list_proposals(
    improvement: SkillImprovementDep,
    status_filter: Annotated[
        Literal["pending", "approved", "rejected", "applied", "stale"] | None,
        Query(alias="status"),
    ] = None,
) -> ProposalList:
    items = improvement.list(status_filter)
    return ProposalList(items=items, total=len(items))
