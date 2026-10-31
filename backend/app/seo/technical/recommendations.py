"""Prioritized recommendations for technical SEO findings.

Method: ``seo-toolkit-prioritization``. No numeric score is produced; every
dimension is an ordinal label with a documented basis:

- **impact**: from the finding's severity, raised one step when the homepage is
  affected or at least half of the audited pages are. It is a planning
  judgment, not a traffic or ranking forecast.
- **confidence**: the catalog's base confidence for the check, lowered one step
  when the finding's evidence is ``inferred``.
- **effort / risk**: the catalog's typical values for the action ("typical",
  not measured for this site).
- **tier**: blocker, cheap_certainty, high_leverage, maintenance, deferred.

Ordering is deterministic: tier, impact, confidence, effort (low first),
affected count (high first), check id.
"""

from dataclasses import dataclass

from app.seo.technical.models import Finding, Level, Recommendation

_LEVELS: tuple[Level, Level, Level] = ("low", "medium", "high")
TIER_ORDER = ("blocker", "cheap_certainty", "high_leverage", "maintenance", "deferred")
SEVERITY_IMPACT: dict[str, Level] = {
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "low",
}
GSC_NOTE = " Google's own view (indexing, canonical chosen) needs Search Console URL Inspection, which AURORA does not connect yet."


@dataclass(frozen=True)
class Action:
    action: str
    effort: Level
    risk: Level
    confidence: Level
    verification: str


CATALOG: dict[str, Action] = {
    "robots.unavailable": Action(
        "Make robots.txt answer 200 (or 404 if you want no rules) instead of an error.",
        "low",
        "low",
        "high",
        "Fetch /robots.txt and confirm a 200 or 404 status; re-run the audit.",
    ),
    "robots.disallows_root": Action(
        "Remove or narrow the rule that disallows '/' for Googlebot (unless the whole site must stay uncrawled).",
        "low",
        "high",
        "high",
        "Re-run the audit; robots.txt must allow '/' for Googlebot." + GSC_NOTE,
    ),
    "robots.blocks_pages": Action(
        "Allow these URLs in robots.txt, or keep them blocked deliberately and stop linking/listing them.",
        "low",
        "medium",
        "high",
        "Re-run the audit; the pages must no longer be reported as disallowed for Googlebot.",
    ),
    "robots.no_sitemap_reference": Action(
        "Add a 'Sitemap: https://…/sitemap.xml' line to robots.txt.",
        "low",
        "low",
        "high",
        "Fetch /robots.txt and confirm the Sitemap line.",
    ),
    "sitemap.missing": Action(
        "Publish an XML sitemap of canonical, indexable URLs and reference it in robots.txt.",
        "medium",
        "low",
        "medium",
        "Re-run the audit; a urlset or sitemapindex must be found and parsed.",
    ),
    "sitemap.invalid": Action(
        "Fix the sitemap so it is valid XML with a <urlset> or <sitemapindex> root and no DOCTYPE.",
        "low",
        "low",
        "high",
        "Re-run the audit; the sitemap must parse without errors.",
    ),
    "sitemap.lists_non_200": Action(
        "List only final, 200-status URLs in the sitemap (replace redirected and error URLs).",
        "low",
        "low",
        "high",
        "Re-run the audit; listed URLs must return 200 without redirects.",
    ),
    "sitemap.lists_noindex": Action(
        "Remove noindex pages from the sitemap, or remove noindex if the pages should be indexed.",
        "low",
        "low",
        "high",
        "Re-run the audit; no sitemap URL may carry noindex.",
    ),
    "sitemap.lists_non_canonical": Action(
        "List the canonical URL of each page in the sitemap.",
        "low",
        "low",
        "high",
        "Re-run the audit; each listed URL must be self-canonical.",
    ),
    "status.5xx": Action(
        "Fix the server errors on these URLs.",
        "medium",
        "low",
        "high",
        "Request each URL and confirm a 2xx status; re-run the audit.",
    ),
    "status.4xx": Action(
        "Restore these pages, redirect them (301) to the closest equivalent, or remove links to them.",
        "medium",
        "medium",
        "high",
        "Request each URL; confirm 2xx, or a single 301 to a relevant 2xx page.",
    ),
    "redirect.chain": Action(
        "Redirect directly to the final URL (one hop) and update internal links to it.",
        "low",
        "low",
        "high",
        "Re-run the audit; each chain must have at most one hop.",
    ),
    "redirect.loop_or_too_long": Action(
        "Break the redirect loop / shorten the chain.",
        "low",
        "low",
        "high",
        "Request the URL; it must reach a 2xx response within one hop.",
    ),
    "redirect.temporary": Action(
        "Use 301/308 for moves that are permanent.",
        "low",
        "low",
        "medium",
        "Re-run the audit; permanent moves must answer 301 or 308.",
    ),
    "security.http_final_url": Action(
        "Serve these pages over HTTPS and redirect HTTP to HTTPS.",
        "medium",
        "medium",
        "high",
        "Request the http:// URL and confirm a 301 to the https:// URL.",
    ),
    "security.mixed_content": Action(
        "Load these resources over HTTPS.",
        "low",
        "low",
        "high",
        "Re-run the audit; no http:// subresources may remain on HTTPS pages.",
    ),
    "indexability.noindex": Action(
        "Remove noindex (meta robots and X-Robots-Tag) from pages that should appear in search; keep it only where intended.",
        "low",
        "high",
        "high",
        "Re-run the audit; intended pages must carry no noindex." + GSC_NOTE,
    ),
    "indexability.meta_refresh": Action(
        "Replace meta refresh with a server-side 301/308 redirect.",
        "low",
        "low",
        "high",
        "Re-run the audit; no meta refresh may remain.",
    ),
    "title.missing": Action(
        "Add a unique, descriptive <title> to each page.",
        "low",
        "low",
        "high",
        "Re-run the audit; every page must have one non-empty title.",
    ),
    "title.multiple": Action(
        "Keep exactly one <title> element per page.",
        "low",
        "low",
        "high",
        "Re-run the audit; each page must have a single title.",
    ),
    "title.duplicate": Action(
        "Write a distinct title for each page that describes that page.",
        "medium",
        "low",
        "high",
        "Re-run the audit; no title may be shared by several pages.",
    ),
    "title.length": Action(
        "Review these titles; keep the important words early (optional).",
        "low",
        "low",
        "medium",
        "Re-run the audit and review the reported lengths.",
    ),
    "description.missing": Action(
        "Add a meta description that summarizes each page.",
        "medium",
        "low",
        "high",
        "Re-run the audit; each page must have a meta description.",
    ),
    "description.duplicate": Action(
        "Write a distinct meta description per page.",
        "medium",
        "low",
        "high",
        "Re-run the audit; descriptions must not be shared.",
    ),
    "description.multiple": Action(
        "Keep one meta description per page.",
        "low",
        "low",
        "high",
        "Re-run the audit; one description per page.",
    ),
    "headings.h1_missing": Action(
        "Add one H1 that states the page topic.",
        "low",
        "low",
        "high",
        "Re-run the audit; each page must have an H1.",
    ),
    "headings.h1_multiple": Action(
        "Consider a single H1 per page (optional).",
        "low",
        "low",
        "medium",
        "Re-run the audit and review the H1 counts.",
    ),
    "images.alt_missing": Action(
        'Add alt text to informative images and alt="" to decorative ones.',
        "medium",
        "low",
        "high",
        "Re-run the audit; no <img> without an alt attribute.",
    ),
    "html.lang_missing": Action(
        'Add <html lang="…"> with the page language.',
        "low",
        "low",
        "high",
        "Re-run the audit; every page must declare lang.",
    ),
    "html.viewport_missing": Action(
        'Add <meta name="viewport" content="width=device-width, initial-scale=1">.',
        "low",
        "low",
        "high",
        "Re-run the audit; every page must have a viewport tag.",
    ),
    "canonical.missing": Action(
        "Add a self-referencing rel=canonical to each indexable page.",
        "low",
        "low",
        "medium",
        "Re-run the audit; each page must declare one canonical." + GSC_NOTE,
    ),
    "canonical.conflicting": Action(
        "Declare a single canonical URL per page.",
        "low",
        "medium",
        "high",
        "Re-run the audit; one canonical value per page.",
    ),
    "canonical.invalid": Action(
        "Use an absolute https:// URL in rel=canonical.",
        "low",
        "low",
        "high",
        "Re-run the audit; canonicals must resolve to valid URLs.",
    ),
    "canonical.points_elsewhere": Action(
        "Confirm each canonical target is the intended preferred page.",
        "low",
        "medium",
        "medium",
        "Review the listed targets." + GSC_NOTE,
    ),
    "canonical.target_not_200": Action(
        "Point canonicals at final, 200-status URLs.",
        "low",
        "medium",
        "high",
        "Re-run the audit; canonical targets must return 200 without redirects.",
    ),
    "canonical.http_header": Action(
        "Keep the Link-header canonical consistent with the HTML canonical.",
        "low",
        "low",
        "medium",
        "Compare the header and HTML values.",
    ),
    "hreflang.invalid_code": Action(
        "Use valid language(-region) codes or x-default in hreflang.",
        "low",
        "low",
        "high",
        "Re-run the audit; no invalid hreflang values.",
    ),
    "hreflang.missing_self": Action(
        "Include a self-referencing hreflang entry on each page in the set.",
        "low",
        "low",
        "high",
        "Re-run the audit.",
    ),
    "hreflang.no_return_link": Action(
        "Make hreflang annotations reciprocal across the set.",
        "medium",
        "low",
        "medium",
        "Re-run the audit with all language versions in scope.",
    ),
    "schema.parse_error": Action(
        "Fix the JSON syntax of the structured data blocks.",
        "low",
        "low",
        "high",
        "Re-run the audit; then test the page in Google's Rich Results Test.",
    ),
    "schema.vocabulary": Action(
        "Add a schema.org @context and an @type to each item.",
        "low",
        "low",
        "high",
        "Re-run the audit; then validate at validator.schema.org.",
    ),
    "schema.missing_required": Action(
        "Add the missing properties only where the facts are visible on the page; otherwise do not use that type.",
        "medium",
        "low",
        "medium",
        "Test the page in Google's Rich Results Test (eligibility is never guaranteed).",
    ),
    "schema.missing_recommended": Action(
        "Add recommended properties that are true and visible on the page (optional).",
        "low",
        "low",
        "medium",
        "Rich Results Test.",
    ),
    "schema.retired_feature": Action(
        "No action needed for search; keep the markup only if it is accurate and useful elsewhere.",
        "low",
        "low",
        "high",
        "None required.",
    ),
    "schema.disagrees_with_page": Action(
        "Make the marked-up headline match the visible title/H1.",
        "low",
        "low",
        "medium",
        "Re-run the audit.",
    ),
    "links.broken_internal": Action(
        "Update or remove internal links to broken URLs (or restore/redirect the targets).",
        "medium",
        "low",
        "high",
        "Re-run the audit; no internal link may point to a 4xx/5xx URL.",
    ),
    "links.redirecting_internal": Action(
        "Update internal links to point at the final URLs.",
        "medium",
        "low",
        "high",
        "Re-run the audit; internal links must not redirect.",
    ),
}
DEFAULT_ACTION = Action(
    "Review the finding and its evidence.",
    "medium",
    "medium",
    "medium",
    "Re-run the audit.",
)


def _step(level: Level, delta: int) -> Level:
    index = max(0, min(2, _LEVELS.index(level) + delta))
    return _LEVELS[index]


def _impact(finding: Finding, home_affected: bool, total_pages: int) -> Level:
    base = SEVERITY_IMPACT[finding.severity]
    share = finding.affected_count / total_pages if total_pages else 0
    if finding.severity != "info" and (home_affected or share >= 0.5):
        base = _step(base, +1)
    return base


def _tier(finding: Finding, impact: Level, confidence: Level, effort: Level) -> str:
    if finding.severity == "high" and impact == "high":
        return "blocker"
    if finding.severity == "info":
        return "deferred"
    if effort == "low" and confidence == "high":
        return "cheap_certainty"
    if impact == "high":
        return "high_leverage"
    return "maintenance"


def build_recommendations(
    findings: list[Finding], *, start_url: str, total_pages: int
) -> list[Recommendation]:
    recs: list[Recommendation] = []
    for finding in findings:
        action = CATALOG.get(finding.check_id, DEFAULT_ACTION)
        confidence = (
            action.confidence
            if finding.evidence_state == "observed"
            else _step(action.confidence, -1)
        )
        impact = _impact(finding, start_url in finding.affected_urls, total_pages)
        tier = _tier(finding, impact, confidence, action.effort)
        recs.append(
            Recommendation(
                finding_id=finding.id,
                check_id=finding.check_id,
                action=action.action,
                rationale=f"{finding.title} ({finding.affected_count} affected). {finding.detail}",
                impact=impact,
                confidence=confidence,
                effort=action.effort,
                risk=action.risk,
                tier=tier,
                affected_count=finding.affected_count,
                verification=action.verification,
                basis=finding.evidence_state,
            )
        )
    rank = {level: i for i, level in enumerate(("high", "medium", "low"))}
    recs.sort(
        key=lambda r: (
            TIER_ORDER.index(r.tier),
            rank[r.impact],
            rank[r.confidence],
            -rank[r.effort],
            -r.affected_count,
            r.check_id,
        )
    )
    for position, rec in enumerate(recs, start=1):
        rec.rank = position
    return recs
