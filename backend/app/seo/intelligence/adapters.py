"""Adapters from existing AURORA SEO services into SEO Intelligence executions.

This is the reuse point: AURORA's technical audit (``seo.technical``) already
crawls, checks and recommends. Importing a finished audit turns its findings into
ledger findings (key ``tech:<check_id>``) so they are compared with history like
any other execution. No new crawling happens here.
"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import ConflictException, ResourceNotFoundException
from app.database.repositories.seo_audit_repository import SeoAuditRepository
from app.seo.intelligence.models import (
    CheckedScope,
    Evidence,
    Execution,
    ExecutionPayload,
    Finding,
    Priority,
    SkillUse,
    ToolUse,
)
from app.seo.intelligence.service import SeoAgentService

TECH_PREFIX = "tech:"
FINISHED = {"completed", "partially_completed"}


def priority_for(severity: str, tier: str | None) -> Priority:
    """Map technical severity/tier onto the four-level SEO Intelligence priority."""
    if tier == "blocker" and severity == "high":
        return "critical"
    if severity in {"high", "medium"}:
        return severity  # type: ignore[return-value]
    return "low"


def _host_matches(host: str, domain: str) -> bool:
    host = host.lower().removeprefix("www.")
    return host == domain.removeprefix("www.")


async def import_technical_audit(
    service: SeoAgentService,
    audits: SeoAuditRepository,
    site_id: str,
    audit_id: UUID,
) -> Execution:
    config = service.website(site_id)
    audit = await audits.get(audit_id)
    if audit is None:
        raise ResourceNotFoundException("Audit not found.")
    if audit.status not in FINISHED:
        raise ConflictException("Only completed or partially completed audits import.")
    if not _host_matches(audit.host, config.domain):
        raise ConflictException(
            f"Audit host {audit.host} does not belong to website {config.domain}."
        )
    rows = await audits.findings(audit_id)
    recs = {r.finding_id: r for r in await audits.recommendations(audit_id)}

    findings: list[Finding] = []
    for row in rows:
        rec = recs.get(row.id)
        evidence = [
            Evidence(
                source=f"AURORA technical audit {audit_id} ({item.get('source', 'audit')})",
                observation=(
                    ", ".join(
                        f"{k}={v}" for k, v in (item.get("observed") or {}).items()
                    )
                    or row.detail
                )[:500],
                url=item.get("url"),
                observed_at=audit.completed_at,
            )
            for item in (row.evidence or [])[:5]
            if isinstance(item, dict)
        ] or [
            Evidence(
                source=f"AURORA technical audit {audit_id}",
                observation=row.detail[:500],
                observed_at=audit.completed_at,
            )
        ]
        findings.append(
            Finding(
                key=f"{TECH_PREFIX}{row.check_id}",
                issue=row.title,
                category=f"technical/{row.category}",
                priority=priority_for(row.severity, rec.tier if rec else None),
                evidence=evidence,
                evidence_state="observed"
                if row.evidence_state == "observed"
                else (
                    "inferred" if row.evidence_state == "inferred" else "not_available"
                ),
                affected_urls=list(row.affected_urls or [])[:50],
                seo_impact=rec.rationale if rec else row.detail,
                recommended_action=rec.action if rec else "Review the audit finding.",
                expected_outcome=(
                    f"The '{row.check_id}' check passes on the affected URLs."
                ),
                verification_method=rec.verification
                if rec
                else "Re-run the AURORA technical audit and confirm the check passes.",
                skill=(row.skill_refs or [None])[0],
            )
        )

    execution = service.start(site_id, "technical-audit-import")
    previous_tech = [
        f.key
        for f in service.repository.load_findings(site_id)
        if f.key.startswith(TECH_PREFIX)
    ]
    complete = audit.status == "completed"
    payload = ExecutionPayload(
        summary=(
            f"Imported AURORA technical audit {audit_id} of {audit.host} "
            f"({audit.status}, {len(findings)} findings)."
        ),
        pages_analysed=[],
        reports_processed=[f"AURORA technical audit {audit_id}"],
        skills_used=[
            SkillUse(
                name="seo-toolkit-technical-audit",
                reason="AURORA's technical audit rules implement this method.",
                analysed=f"{(audit.coverage or {}).get('pages_audited', 0)} audited pages",
                actions=["imported findings and recommendations"],
                output=f"{len(findings)} findings",
            )
        ],
        tools_used=[
            ToolUse(
                name="AURORA TechnicalSeoService (stored audit)",
                kind="aurora",
                status="success" if complete else "partial",
                calls=1,
                note=(audit.coverage or {}).get("stopped_reason") or "",
            )
        ],
        findings=findings,
        # A full audit re-runs every rule, so absent tech:* findings are resolved.
        # A partial audit proves nothing about what it did not reach.
        checked=CheckedScope(keys=previous_tech if complete else []),
        blockers=[] if complete else ["Audit stopped early; coverage is partial."],
    )
    service.record(site_id, execution.id, payload)
    finished, _ = service.finish(
        site_id, execution.id, "completed" if complete else "partial"
    )
    return finished
