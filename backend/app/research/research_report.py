"""Deterministic research reports (structured JSON + Markdown).

Reports are rendered from structured, evidence-linked data only. Fields without
evidence are shown as "not publicly found"; the builder never invents
strengths, weaknesses, market share, revenue, customer counts or rankings, and
no LLM is involved. Competitor relationships are *not* inferred: companies
researched together are only listed as a comparison set.
"""

import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.core.logging import log_event
from app.research.domains import registrable_domain
from app.schemas.intelligence import (
    NOT_FOUND,
    ClaimConflict,
    CompanyReportEntry,
    EntityType,
    EvidenceCoverage,
    GeneratedReport,
    ReportSource,
    ReportType,
    ResearchClaim,
    ResearchReportData,
    ResolvedEntity,
    SourceKind,
    VerificationStatus,
    WebsiteCandidate,
)

logger = logging.getLogger(__name__)

_BACKED = (VerificationStatus.SUPPORTED, VerificationStatus.PARTIALLY_SUPPORTED)
_SHOWN = _BACKED + (VerificationStatus.CONFLICTING,)


def _dedupe(items: list[str], limit: int = 40) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out[:limit]


class ReportBuilder:
    """Build reports from resolved entities and verified claims."""

    def build(
        self,
        report_type: ReportType,
        subject: str,
        websites: list[WebsiteCandidate],
        entities: list[ResolvedEntity],
        claims: list[ResearchClaim],
        conflicts: list[ClaimConflict],
        coverage: EvidenceCoverage,
        pricing_pages_checked: dict[str, int] | None = None,
        notes: list[str] | None = None,
    ) -> GeneratedReport:
        """Return the structured report and its Markdown rendering."""
        checked = pricing_pages_checked or {}
        by_scope = self._group_by_scope(entities, claims)
        entries = [
            self._company_entry(
                site, by_scope, checked, self._coverage_for(site, claims, entities)
            )
            for site in websites
        ]
        if report_type in (ReportType.COMPETITOR_PROFILE, ReportType.INDUSTRY_REPORT):
            for entry in entries:
                others = [e.name for e in entries if e is not entry]
                if others:
                    entry.competitor_relationships = [
                        "Researched alongside: "
                        + ", ".join(others)
                        + " (no competitive relationship is asserted from evidence)"
                    ]
        data = ResearchReportData(
            report_type=report_type,
            subject=subject,
            companies=entries,
            comparison=self._comparison_rows(report_type, entries, entities),
            sources=self._sources(claims),
            unverified_information=self._unverified(claims, websites),
            conflicting_information=[
                f"{c.subject_entity}: {c.predicate.replace('_', ' ')} - "
                + " | ".join(c.values)
                for c in conflicts
            ],
            evidence_coverage=coverage,
            notes=(notes or [])
            + [
                "Facts are limited to the pages that were crawled; "
                f"'{NOT_FOUND}' means not found in those pages, not that it does not exist.",
                "Vendor statements are recorded as statements; they are not independently verified.",
            ],
        )
        markdown = render_markdown(data)
        log_event(
            logger,
            "report_generated",
            report_type=report_type.value,
            companies=len(entries),
            claims=coverage.total_claims,
            coverage=coverage.coverage_ratio,
        )
        return GeneratedReport(report_type=report_type, data=data, markdown=markdown)

    # -- grouping ---------------------------------------------------------------

    @staticmethod
    def _group_by_scope(
        entities: list[ResolvedEntity], claims: list[ResearchClaim]
    ) -> dict[str, dict[str, Any]]:
        scopes: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"entities": [], "claims": []}
        )
        for entity in entities:
            if entity.scope_domain:
                scopes[registrable_domain(entity.scope_domain)]["entities"].append(
                    entity
                )
        for claim in claims:
            if claim.scope_domain:
                scopes[registrable_domain(claim.scope_domain)]["claims"].append(claim)
        return scopes

    @staticmethod
    def _coverage_for(
        site: WebsiteCandidate,
        claims: list[ResearchClaim],
        entities: list[ResolvedEntity],
    ) -> EvidenceCoverage:
        from app.research.evidence_manager import EvidenceManager

        scope = registrable_domain(site.domain)
        site_claims = [
            c
            for c in claims
            if c.scope_domain and registrable_domain(c.scope_domain) == scope
        ]
        site_entities = [
            e
            for e in entities
            if e.scope_domain and registrable_domain(e.scope_domain) == scope
        ]
        return EvidenceManager.coverage(site_claims, site_entities)

    # -- company entry ------------------------------------------------------------

    def _company_entry(
        self,
        site: WebsiteCandidate,
        by_scope: dict[str, dict[str, Any]],
        checked: dict[str, int],
        coverage: EvidenceCoverage,
    ) -> CompanyReportEntry:
        scope = registrable_domain(site.domain)
        bucket = by_scope.get(scope, {"entities": [], "claims": []})
        entities: list[ResolvedEntity] = bucket["entities"]
        claims: list[ResearchClaim] = bucket["claims"]
        entry = CompanyReportEntry(
            name=site.website_name,
            official_website=site.homepage_url,
            official_status=site.official_status,
            evidence_coverage=coverage,
        )
        entry.overview = self._value(claims, "description")
        entry.industry = self._value(claims, "industry")
        entry.headquarters = self._value(claims, "headquarters")
        entry.founded_year = self._value(claims, "founded_year")
        entry.company_size = self._value(claims, "company_size")

        def named(kind: EntityType) -> list[str]:
            return _dedupe(
                [e.canonical_name for e in entities if e.entity_type == kind]
            )

        entry.products = named(EntityType.PRODUCT)
        entry.services = named(EntityType.SERVICE)
        entry.features = named(EntityType.FEATURE)
        entry.target_customers = named(EntityType.TARGET_CUSTOMER)
        entry.technologies = [
            f"{t} (mentioned on the website)"
            for t in _dedupe(
                [
                    c.object_value
                    for c in claims
                    if c.predicate == "mentions_technology"
                    and c.verification_status in _BACKED
                ]
            )
        ]
        entry.use_cases = _dedupe(
            [
                u
                for e in entities
                if e.entity_type == EntityType.PRODUCT
                for u in e.attributes.get("use_cases", [])
            ]
        )
        entry.integrations = _dedupe(
            [
                i
                for e in entities
                if e.entity_type == EntityType.PRODUCT
                for i in e.attributes.get("integrations", [])
            ]
            + [
                c.object_value
                for c in claims
                if c.predicate == "integration" and c.verification_status in _BACKED
            ]
        )
        entry.pricing = self._pricing_lines(entities, claims)
        pages = checked.get(scope, 0)
        if entry.pricing:
            entry.pricing_status = "found"
        elif pages:
            entry.pricing_status = f"{NOT_FOUND} (checked {pages} pricing page(s))"
        else:
            entry.pricing_status = f"{NOT_FOUND} (no pricing page was crawled)"
        entry.source_urls = sorted(
            {c.source_url for c in claims if c.verification_status in _SHOWN}
        )
        return entry

    @staticmethod
    def _value(claims: list[ResearchClaim], predicate: str) -> str:
        """Show a value only if a claim backs it; surface conflicts instead of picking."""
        relevant = [
            c
            for c in claims
            if c.predicate == predicate and c.verification_status in _SHOWN
        ]
        if not relevant:
            return NOT_FOUND
        if any(
            c.verification_status == VerificationStatus.CONFLICTING for c in relevant
        ):
            return "conflicting evidence: " + " | ".join(
                sorted({c.object_value for c in relevant})
            )
        official = [c for c in relevant if c.source_kind == SourceKind.OFFICIAL]
        return (official or relevant)[0].object_value

    @staticmethod
    def _pricing_lines(
        entities: list[ResolvedEntity], claims: list[ResearchClaim]
    ) -> list[str]:
        backed_sources = {
            c.source_url
            for c in claims
            if c.predicate == "price" and c.verification_status in _SHOWN
        }
        lines: list[str] = []
        for entity in entities:
            if entity.entity_type != EntityType.PRICING:
                continue
            attrs = entity.attributes
            source = attrs.get("source_url") or (
                entity.source_urls[0] if entity.source_urls else ""
            )
            if source not in backed_sources:
                continue
            amount = attrs.get("price_amount")
            parts = [
                attrs.get("plan_name") or attrs.get("product_or_service") or "Plan",
                f"{attrs.get('currency') or ''}{amount}"
                if amount is not None
                else "price not stated",
            ]
            if attrs.get("billing_interval"):
                parts.append(f"per {attrs['billing_interval']}")
            verified = str(attrs.get("last_verified_at", ""))[:10]
            lines.append(
                f"{' - '.join(parts[:2])} {' '.join(parts[2:])} (source: {source}; verified {verified})".replace(
                    "  ", " "
                )
            )
        return _dedupe(lines)

    # -- comparison and sources ----------------------------------------------------

    @staticmethod
    def _comparison_rows(
        report_type: ReportType,
        entries: list[CompanyReportEntry],
        entities: list[ResolvedEntity],
    ) -> list[dict[str, Any]]:
        if report_type == ReportType.PRODUCT_PROFILE:
            rows = []
            for entity in entities:
                if entity.entity_type != EntityType.PRODUCT:
                    continue
                company = next(
                    (
                        e.name
                        for e in entries
                        if e.official_website
                        and entity.scope_domain
                        and registrable_domain(entity.scope_domain)
                        in e.official_website
                    ),
                    NOT_FOUND,
                )
                attrs = entity.attributes
                rows.append(
                    {
                        "product": entity.canonical_name,
                        "company": company,
                        "category": attrs.get("category") or NOT_FOUND,
                        "features": attrs.get("features") or [],
                        "use_cases": attrs.get("use_cases") or [],
                        "integrations": attrs.get("integrations") or [],
                        "official_product_url": attrs.get("product_url") or NOT_FOUND,
                        "sources": entity.source_urls,
                    }
                )
            return rows
        if report_type == ReportType.SERVICE_PROFILE:
            return [
                {
                    "service": e.canonical_name,
                    "description": e.attributes.get("description") or NOT_FOUND,
                    "deliverables": e.attributes.get("deliverables") or [],
                    "service_url": e.attributes.get("service_url") or NOT_FOUND,
                    "sources": e.source_urls,
                }
                for e in entities
                if e.entity_type == EntityType.SERVICE
            ]
        if report_type == ReportType.TECHNOLOGY_PROFILE:
            return [
                {
                    "technology": e.canonical_name,
                    "documentation_urls": e.attributes.get("documentation_urls") or [],
                    "sources": e.source_urls,
                }
                for e in entities
                if e.entity_type == EntityType.TECHNOLOGY
            ]
        return [
            {
                "name": e.name,
                "website": e.official_website,
                "products": e.products,
                "services": e.services,
                "pricing_model": e.pricing_status
                if not e.pricing
                else "; ".join(e.pricing[:3]),
                "target_audience": e.target_customers or [NOT_FOUND],
                "features": e.features[:10],
                "documented_differentiators": e.strengths or [NOT_FOUND],
                "sources": e.source_urls[:5],
            }
            for e in entries
        ]

    @staticmethod
    def _sources(claims: list[ResearchClaim]) -> list[ReportSource]:
        seen: dict[str, ReportSource] = {}
        for claim in claims:
            if claim.verification_status in _SHOWN and claim.source_url not in seen:
                seen[claim.source_url] = ReportSource(
                    url=claim.source_url,
                    title=claim.source_title,
                    domain=claim.source_domain,
                    kind=claim.source_kind,
                    observed_at=claim.observed_at,
                )
        return list(seen.values())

    @staticmethod
    def _unverified(
        claims: list[ResearchClaim], websites: list[WebsiteCandidate]
    ) -> list[str]:
        items = [
            f"Official ownership of {w.homepage_url} could not be verified."
            for w in websites
            if w.official_status.value == "unverified"
        ]
        items += [
            f"{c.claim_text} ({c.verification_status.value}: {'; '.join(c.notes[-1:])})"
            for c in claims
            if c.verification_status
            in (VerificationStatus.UNVERIFIED, VerificationStatus.UNSUPPORTED)
        ]
        return items[:100]


# -- Markdown rendering -------------------------------------------------------------


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else f"- {NOT_FOUND}"


def _render_company(entry: CompanyReportEntry) -> str:
    cov = entry.evidence_coverage
    lines = [
        f"## {entry.name}",
        "",
        f"- **Official website:** {entry.official_website or NOT_FOUND} "
        f"({entry.official_status.value})",
        f"- **Overview:** {entry.overview}",
        f"- **Industry:** {entry.industry}",
        f"- **Headquarters:** {entry.headquarters}",
        f"- **Founded:** {entry.founded_year}",
        f"- **Company size:** {entry.company_size}",
        f"- **Pricing:** {entry.pricing_status}",
        "",
        "### Products",
        _bullets(entry.products),
        "",
        "### Services",
        _bullets(entry.services),
        "",
        "### Main features",
        _bullets(entry.features),
        "",
        "### Technology (named on the website)",
        _bullets(entry.technologies),
        "",
        "### Pricing information",
        _bullets(entry.pricing),
        "",
        "### Target customers",
        _bullets(entry.target_customers),
        "",
        "### Use cases",
        _bullets(entry.use_cases),
        "",
        "### Integrations",
        _bullets(entry.integrations),
        "",
        "### Strengths (evidence-backed only)",
        _bullets(entry.strengths),
        "",
        "### Limitations (evidence-backed only)",
        _bullets(entry.limitations),
        "",
        "### Competitor relationships",
        _bullets(entry.competitor_relationships),
        "",
        f"*Evidence coverage: {cov.coverage_ratio:.0%} of {cov.total_claims} claims backed "
        f"({cov.supported} supported, {cov.partially_supported} partially, "
        f"{cov.conflicting} conflicting, {cov.unsupported} unsupported, {cov.unverified} unverified).*",
        "",
    ]
    return "\n".join(lines)


def _table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"{NOT_FOUND}\n"
    headers = list(rows[0])
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        cells = []
        for h in headers:
            value = row.get(h)
            text = "; ".join(map(str, value)) if isinstance(value, list) else str(value)
            cells.append((text or NOT_FOUND).replace("|", "/").replace("\n", " "))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def render_markdown(data: ResearchReportData) -> str:
    """Render a report from structured data (deterministic)."""
    title = data.report_type.value.replace("_", " ").title()
    stamp = data.research_date.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    parts = [f"# {title}: {data.subject}", "", f"*Research date: {stamp}*", ""]
    if data.report_type in (ReportType.COMPANY_PROFILE,):
        parts += [_render_company(c) for c in data.companies]
    else:
        parts += ["## Comparison", "", _table(data.comparison)]
        if data.report_type in (
            ReportType.COMPETITOR_PROFILE,
            ReportType.INDUSTRY_REPORT,
        ):
            parts += [_render_company(c) for c in data.companies]
    cov = data.evidence_coverage
    parts += [
        "## Evidence coverage",
        "",
        f"{cov.coverage_ratio:.0%} of {cov.total_claims} claims are backed by retrieved "
        f"evidence ({cov.supported} supported, {cov.partially_supported} partially supported, "
        f"{cov.conflicting} conflicting, {cov.unsupported} unsupported, {cov.unverified} unverified).",
        "",
        "## Conflicting information",
        _bullets(data.conflicting_information),
        "",
        "## Unverified information",
        _bullets(data.unverified_information),
        "",
        "## Sources",
        "",
    ]
    parts += [
        f"- {s.url} ({s.kind.value}{', ' + s.title if s.title else ''})"
        for s in data.sources
    ] or [f"- {NOT_FOUND}"]
    parts += ["", "## Notes", _bullets(data.notes), ""]
    return "\n".join(parts)


def default_report_type(subject_type: str) -> ReportType:
    """Pick a sensible report type for a request's subject type."""
    return {
        "company": ReportType.COMPANY_PROFILE,
        "product": ReportType.PRODUCT_PROFILE,
        "service": ReportType.SERVICE_PROFILE,
        "technology": ReportType.TECHNOLOGY_PROFILE,
        "competitor": ReportType.COMPETITOR_PROFILE,
    }.get(subject_type, ReportType.INDUSTRY_REPORT)


def now() -> datetime:
    """UTC now (kept here so tests can patch report timestamps if needed)."""
    return datetime.now(UTC)
