"""Evidence-backed recommendations from Search Console findings.

Method: ``seo-toolkit-prioritization``. Everything is an ordinal label with a stated
basis; no numeric score is produced.

- **impact** is the share of the property's own traffic at stake: lost or missing
  clicks (low CTR, declines) as a share of the property's clicks in the earlier
  window, or impressions at stake (new queries, cannibalization) as a share of its
  impressions. 5% or more of clicks / 2% of impressions is high, 1% / 0.5% medium.
  It is a planning judgment, not a traffic forecast.
- **confidence** starts from how direct the evidence is and drops one step when a
  window has missing days.
- **effort** and **risk** are typical values for the action, not measurements.
- **tier**: ``cheap_certainty``, ``high_leverage``, ``investigate`` (find the cause
  before acting), ``maintenance`` or ``info``.

A recommendation never changes a site. Content actions are proposals for a refreshed
**draft**, and publishing any update needs a new approval.
"""

from collections.abc import Callable
from typing import Any

from app.monitoring.models import InsightDraft, Level, Window

_LEVELS: tuple[Level, Level, Level] = ("low", "medium", "high")
TIER_ORDER = ("high_leverage", "cheap_certainty", "investigate", "maintenance", "info")
CLICK_SHARES = (0.05, 0.01)
IMPRESSION_SHARES = (0.02, 0.005)


def _lower(level: Level) -> Level:
    return _LEVELS[max(0, _LEVELS.index(level) - 1)]


def impact_from_share(share: float, cuts: tuple[float, float]) -> Level:
    return "high" if share >= cuts[0] else "medium" if share >= cuts[1] else "low"


def _confidence(base: Level, window: Window) -> Level:
    if window.coverage("current") < 0.9 or window.coverage("previous") < 0.9:
        return _lower(base)
    return base


def build_insights(
    findings: dict[str, list[dict[str, Any]]],
    *,
    window: Window,
    previous_clicks: float,
    current_impressions: float,
    article_for_page: Callable[[str], str | None],
    comparison_possible: bool,
    history_windows: float,
) -> list[InsightDraft]:
    """``findings`` maps a kind to detector output; returns ranked insights."""
    out: list[InsightDraft] = []
    when = f"{window.current_start} to {window.current_end}"
    versus = f"{window.previous_start} to {window.previous_end}"

    def click_impact(clicks: float) -> Level:
        return (
            impact_from_share(clicks / previous_clicks, CLICK_SHARES)
            if previous_clicks
            else "low"
        )

    def impression_impact(impressions: float) -> Level:
        return (
            impact_from_share(impressions / current_impressions, IMPRESSION_SHARES)
            if current_impressions
            else "low"
        )

    for f in findings.get("low_ctr", []):
        base: Level = (
            "high"
            if f["baseline_rows"] >= 100 and f["impressions"] >= 500
            else "medium"
        )
        subject = f["page"] or f["query"] or "property"
        out.append(
            InsightDraft(
                kind="low_ctr",
                subject_type="page" if f["page"] else "query",
                subject=subject,
                evidence={**f, "window": when},
                impact=click_impact(f["shortfall_clicks"]),
                confidence=_lower(base) if window.coverage("current") < 0.9 else base,
                effort="low",
                risk="low",
                tier="cheap_certainty",
                basis="measured",
                action=(
                    f"Test a title and meta description for {subject} that matches the intent of "
                    f"the query '{f['query']}'."
                    if f["query"]
                    else f"Test a clearer title and meta description for {subject}."
                ),
                rationale=(
                    f"{f['impressions']:.0f} impressions at average position {f['position']} but "
                    f"CTR {f['ctr']:.1%}, below half of this property's own CTR at that position "
                    f"({f['baseline_ctr']:.1%} from {f['baseline_rows']} rows). About "
                    f"{f['shortfall_clicks']:.0f} clicks fewer than the property's own baseline. "
                    "Why the snippet underperforms is a hypothesis."
                ),
                verification=(
                    "After the change, compare CTR for the same page and query over a 28-day "
                    "window while average position stays within about 1 of today's."
                ),
                refresh_candidate=True,
                article_id=article_for_page(f["page"]) if f["page"] else None,
            )
        )

    if comparison_possible:
        for f in findings.get("declining_clicks", []):
            confidence = _confidence(
                "medium" if f["driver"] != "mixed" else "low", window
            )
            out.append(
                InsightDraft(
                    kind="declining_clicks",
                    subject_type="page",
                    subject=f["page"],
                    evidence={**f, "window": when, "compared_with": versus},
                    impact=click_impact(f["lost_clicks"]),
                    confidence=confidence,
                    effort="medium",
                    risk="medium",
                    tier="investigate",
                    basis="measured",
                    action=f"Investigate why {f['page']} lost clicks before changing it (main driver: "
                    f"{f['driver'].replace('_', ' ')}).",
                    rationale=(
                        f"Clicks fell from {f['clicks_previous']:.0f} to {f['clicks_current']:.0f} "
                        f"({f['clicks_change']:.0%}); impressions {f['impressions_change'] if f['impressions_change'] is not None else 'n/a'}, "
                        f"position {f['position_previous']} to {f['position_current']}. The driver is "
                        "inferred from which of impressions, CTR and position moved; the cause is a hypothesis."
                    ),
                    verification=(
                        "In Search Console, filter Performance by this page and compare the two "
                        "periods by query; check the live results for the top queries."
                    ),
                    refresh_candidate=f["driver"] in ("ranking_loss", "ctr_drop"),
                    article_id=article_for_page(f["page"]),
                )
            )
        for f in findings.get("declining_position", []):
            out.append(
                InsightDraft(
                    kind="declining_position",
                    subject_type="page",
                    subject=f["page"],
                    evidence={**f, "window": when, "compared_with": versus},
                    impact=click_impact(
                        max(0.0, f["clicks_previous"] - f["clicks_current"])
                    ),
                    confidence=_confidence("medium", window),
                    effort="medium",
                    risk="medium",
                    tier="investigate",
                    basis="measured",
                    action=f"Investigate the drop in average position for {f['page']}.",
                    rationale=(
                        f"Average position moved from {f['position_previous']} to "
                        f"{f['position_current']} ({f['position_change']:+} positions) with "
                        f"{f['impressions_current']:.0f} impressions. Average position is not exact "
                        "rank: a change in which queries bring impressions can move it."
                    ),
                    verification="Compare the page's top queries and their positions across the two periods.",
                    refresh_candidate=True,
                    article_id=article_for_page(f["page"]),
                )
            )
        for f in findings.get("growing_page", []):
            out.append(
                InsightDraft(
                    kind="growing_page",
                    subject_type="page",
                    subject=f["page"],
                    evidence={**f, "window": when, "compared_with": versus},
                    impact="low",
                    confidence=_confidence("medium", window),
                    effort="low",
                    risk="low",
                    tier="info",
                    basis="measured",
                    action=f"No fix needed. {f['page']} is gaining visibility; consider linking to it from related pages.",
                    rationale=f"Clicks rose from {f['clicks_previous']:.0f} to {f['clicks_current']:.0f} ({f['clicks_change']:+.0%}).",
                    verification="Check again next period to see whether the gain holds.",
                    article_id=article_for_page(f["page"]),
                )
            )

    for f in findings.get("new_query", []):
        base = "medium" if history_windows >= 2 else "low"
        out.append(
            InsightDraft(
                kind="new_query",
                subject_type="query",
                subject=f["query"],
                evidence={**f, "window": when},
                impact=impression_impact(f["impressions"]),
                confidence=base,
                effort="medium",
                risk="low",
                tier="high_leverage"
                if impression_impact(f["impressions"]) != "low"
                else "maintenance",
                basis="measured",
                action=(
                    f"Cover the query '{f['query']}' on {f['top_page']} (add a section that answers it), "
                    "or plan a new article if its intent differs."
                ),
                rationale=(
                    f"New in the stored data: {f['impressions']:.0f} impressions at average position "
                    f"{f['position']}, {f['clicks']:.0f} clicks. 'New' means not seen earlier in the "
                    "stored history; the history may start after it first appeared."
                ),
                verification="Re-check the query's impressions, clicks and position after the update.",
                refresh_candidate=bool(f["top_page"]),
                article_id=article_for_page(f["top_page"]) if f["top_page"] else None,
            )
        )

    for f in findings.get("cannibalization", []):
        names = ", ".join(p["page"] for p in f["pages"])
        out.append(
            InsightDraft(
                kind="cannibalization",
                subject_type="query",
                subject=f["query"],
                evidence={**f, "window": when},
                impact=impression_impact(f["total_impressions"]),
                confidence="low",
                effort="high",
                risk="high",
                tier="investigate",
                basis="inferred",
                action=(
                    f"Review whether these pages serve the same intent for '{f['query']}': {names}. "
                    "Only if they do, differentiate them or plan a consolidation (needs approval)."
                ),
                rationale=(
                    f"{len(f['pages'])} pages each receive at least {f['pages'][-1]['share']:.0%} of the "
                    f"{f['total_impressions']:.0f} impressions for this query. Sharing a query is not "
                    "proof of a problem: Google may show several URLs on purpose."
                ),
                verification=(
                    "Search the query and compare the pages' purpose; check whether the "
                    "ranking page alternates over time before consolidating anything."
                ),
                refresh_candidate=False,
            )
        )

    out.sort(
        key=lambda i: (
            TIER_ORDER.index(i.tier),
            _LEVELS[::-1].index(i.impact),
            _LEVELS[::-1].index(i.confidence),
            i.kind,
            i.subject,
        )
    )
    for position, insight in enumerate(out, start=1):
        insight.rank = position
    return out


def no_data_insight(
    page: str, state: dict[str, Any], article_id: str | None
) -> InsightDraft:
    """A mapped page without stored data: an explanation, never a performance claim."""
    return InsightDraft(
        kind="no_data_yet",
        subject_type="page",
        subject=page,
        evidence=state,
        impact="low",
        confidence="high",
        effort="low",
        risk="low",
        tier="info",
        basis="measured",
        action=(
            "Wait and re-sync; do not change the page because of missing data."
            if state["state"] in ("too_new", "has_data")
            else "Re-sync, then check in Search Console whether the page is indexed."
        ),
        rationale=state.get("note", "No stored Search Console rows for this page."),
        verification="Run another sync after a few days and re-run the analysis.",
        article_id=article_id,
    )
