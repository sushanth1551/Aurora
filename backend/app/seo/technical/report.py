"""Deterministic technical SEO report (JSON and Markdown) from stored records.

No LLM is involved: the report only restates stored findings, evidence counts,
recommendations and coverage, including what was *not* measured.
"""

from typing import Any

from app.database.models import (
    SeoAuditFindingRecord,
    SeoAuditRecommendationRecord,
    SeoAuditRecord,
)
from app.seo.technical.models import INDEXABILITY_NOTE


def build_report(
    audit: SeoAuditRecord,
    findings: list[SeoAuditFindingRecord],
    recommendations: list[SeoAuditRecommendationRecord],
) -> dict[str, Any]:
    by_id = {f.id: f for f in findings}
    return {
        "audit_id": str(audit.id),
        "site_url": audit.site_url,
        "status": audit.status,
        "generated_from": "stored audit results (deterministic; no LLM)",
        "started_at": audit.started_at.isoformat() if audit.started_at else None,
        "completed_at": audit.completed_at.isoformat() if audit.completed_at else None,
        "summary": audit.summary,
        "coverage": audit.coverage,
        "usage": audit.usage,
        "site_files": audit.site_files,
        "indexability_note": INDEXABILITY_NOTE,
        "recommendations": [
            {
                "rank": r.rank,
                "tier": r.tier,
                "action": r.action,
                "impact": r.impact,
                "confidence": r.confidence,
                "effort": r.effort,
                "risk": r.risk,
                "affected_count": r.affected_count,
                "verification": r.verification,
                "basis": r.basis,
                "finding": {
                    "check_id": r.check_id,
                    "title": by_id[r.finding_id].title
                    if r.finding_id in by_id
                    else None,
                },
            }
            for r in recommendations
        ],
        "findings": [
            {
                "check_id": f.check_id,
                "category": f.category,
                "severity": f.severity,
                "title": f.title,
                "detail": f.detail,
                "affected_count": f.affected_count,
                "affected_urls": f.affected_urls[:50],
                "evidence_items": len(f.evidence),
                "evidence_state": f.evidence_state,
                "skill_refs": f.skill_refs,
            }
            for f in findings
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Technical SEO audit: {report['site_url']}", ""]
    lines.append(
        f"Status: **{report['status']}** · completed {report['completed_at'] or 'n/a'} · "
        "generated deterministically from stored results (no LLM)."
    )
    coverage = report.get("coverage") or {}
    usage = report.get("usage") or {}
    lines += [
        "",
        "## Coverage",
        "",
        f"- Pages audited: {coverage.get('pages_audited', 0)} of {coverage.get('pages_discovered', 0)} discovered"
        f" (sitemap URLs found: {coverage.get('sitemap_urls_found', 0)})",
        f"- Internal link targets: {coverage.get('link_targets_found', 0)} found, "
        f"{coverage.get('link_targets_checked', 0)} checked directly",
        f"- Firecrawl scrapes used: {usage.get('firecrawl_scrapes', 0)} "
        f"(failed: {usage.get('firecrawl_failures', 0)}); direct requests: {usage.get('direct_requests', 0)}",
    ]
    if coverage.get("stopped_reason"):
        lines.append(
            f"- **Stopped early:** {coverage['stopped_reason']} (results are partial)"
        )
    lines += ["", "Not measured:"] + [
        f"- {item}" for item in coverage.get("not_measured", [])
    ]
    lines += ["", f"> {report['indexability_note']}", ""]

    lines += ["## Recommendations (in priority order)", ""]
    if not report["recommendations"]:
        lines.append(
            "No recommendations: no rule produced a finding for the audited pages."
        )
    for rec in report["recommendations"]:
        lines.append(
            f"{rec['rank']}. **{rec['action']}** — tier `{rec['tier']}`, impact {rec['impact']}, "
            f"confidence {rec['confidence']}, effort {rec['effort']}, risk {rec['risk']}; "
            f"{rec['affected_count']} affected ({rec['finding']['check_id']})."
        )
        lines.append(f"   - Verify: {rec['verification']}")
    lines += ["", "## Findings", ""]
    for finding in report["findings"]:
        lines.append(
            f"### [{finding['severity']}] {finding['title']} (`{finding['check_id']}`)"
        )
        lines.append("")
        lines.append(finding["detail"])
        lines.append("")
        lines.append(
            f"Affected: {finding['affected_count']} · evidence items: {finding['evidence_items']} "
            f"({finding['evidence_state']}) · method: {', '.join(finding['skill_refs'])}"
        )
        for url in finding["affected_urls"][:10]:
            lines.append(f"- {url}")
        if finding["affected_count"] > 10:
            lines.append(f"- … and {finding['affected_count'] - 10} more (see the API)")
        lines.append("")
    lines.append(
        "Impact, confidence, effort and risk are ordinal planning labels, not measurements or "
        "ranking forecasts. No result here guarantees indexing or rankings."
    )
    return "\n".join(lines) + "\n"
