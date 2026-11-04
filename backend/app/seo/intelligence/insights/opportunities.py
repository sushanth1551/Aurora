"""Quick wins, content decay and page value from Search Console (pure functions).

- **Striking distance**: query/page pairs ranking 4-20 with real impressions.
- **Low CTR at a good position**: top-5 rankings whose CTR is far below the
  site's *own* CTR at that position (a title/snippet problem, not a ranking one).
- **Content decay**: pages whose clicks fell against both the previous window and
  their own best window, with the queries that lost the clicks.
- **Prune review**: known pages with no impressions at all for 90 days.
- **Merge candidates**: pages that split the impressions of the same queries.
- **Page value**: an imported conversions file (GA4 export or CRM) turns extra
  clicks into extra leads, so work can be ranked by leads instead of traffic.

"Extra clicks" use the site's own CTR curve measured in the same data; when the
data is too thin for a position the value is ``None`` (never a generic curve).
Removing, merging or redirecting pages is always the owner's decision.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from app.seo.intelligence.insights.common import (
    GscRow,
    evidence,
    finding,
    page_path,
    pct_change,
)
from app.seo.intelligence.models import Finding, Priority

SKILL = "seo-opportunities"
CURVE_MIN_IMPRESSIONS = 200
STRIKING_LOW, STRIKING_HIGH = 4.0, 20.0
TARGET_POSITION = 3


# --- CTR curve ------------------------------------------------------------------------


def ctr_curve(
    rows: list[GscRow], min_impressions: int = CURVE_MIN_IMPRESSIONS
) -> dict[int, float]:
    """The site's measured CTR per rounded position (1-20), from query/page rows."""
    clicks: dict[int, float] = defaultdict(float)
    impressions: dict[int, float] = defaultdict(float)
    for row in rows:
        bucket = int(round(row.position))
        if 1 <= bucket <= 20:
            clicks[bucket] += row.clicks
            impressions[bucket] += row.impressions
    return {
        b: clicks[b] / impressions[b]
        for b in sorted(impressions)
        if impressions[b] >= min_impressions
    }


def expected_ctr(curve: dict[int, float], position: float) -> float | None:
    bucket = int(round(position))
    for candidate in (bucket, bucket - 1, bucket + 1):
        if candidate in curve:
            return curve[candidate]
    return None


def target_ctr(curve: dict[int, float], target: int = TARGET_POSITION) -> float | None:
    """CTR at the target position, or the best measured position above it."""
    for position in range(target, 0, -1):
        if position in curve:
            return curve[position]
    return None


# --- Opportunities ----------------------------------------------------------------------


@dataclass
class Opportunity:
    kind: str  # striking-distance | low-ctr
    query: str
    page: str
    impressions: float
    clicks: float
    position: float
    ctr: float
    reference_ctr: float | None
    extra_clicks: float | None
    extra_leads: float | None = None


def striking_distance(
    rows: list[GscRow],
    curve: dict[int, float],
    *,
    min_impressions: int = 100,
    low: float = STRIKING_LOW,
    high: float = STRIKING_HIGH,
) -> list[Opportunity]:
    goal = target_ctr(curve)
    out = []
    for row in rows:
        if row.impressions < min_impressions or not low <= row.position <= high:
            continue
        extra = None if goal is None else max(0.0, row.impressions * (goal - row.ctr))
        out.append(
            Opportunity(
                kind="striking-distance",
                query=row.get("query"),
                page=page_path(row.get("page")),
                impressions=row.impressions,
                clicks=row.clicks,
                position=round(row.position, 1),
                ctr=round(row.ctr, 4),
                reference_ctr=None if goal is None else round(goal, 4),
                extra_clicks=None if extra is None else round(extra, 1),
            )
        )
    return sorted(out, key=lambda o: (o.extra_clicks or 0, o.impressions), reverse=True)


def low_ctr_scope(
    rows: list[GscRow], *, max_position: float = 5.0, min_impressions: int = 200
) -> set[str]:
    """Pages the low-CTR test could evaluate (it only judges these)."""
    return {
        page_path(r.get("page"))
        for r in rows
        if r.impressions >= min_impressions and r.position <= max_position
    }


def low_ctr(
    rows: list[GscRow],
    curve: dict[int, float],
    *,
    max_position: float = 5.0,
    min_impressions: int = 200,
    ratio: float = 0.5,
) -> list[Opportunity]:
    """Good rankings that earn less than ``ratio`` of the site's own CTR there."""
    out = []
    for row in rows:
        if row.impressions < min_impressions or row.position > max_position:
            continue
        reference = expected_ctr(curve, row.position)
        if reference is None or row.ctr >= reference * ratio:
            continue
        out.append(
            Opportunity(
                kind="low-ctr",
                query=row.get("query"),
                page=page_path(row.get("page")),
                impressions=row.impressions,
                clicks=row.clicks,
                position=round(row.position, 1),
                ctr=round(row.ctr, 4),
                reference_ctr=round(reference, 4),
                extra_clicks=round(row.impressions * (reference - row.ctr), 1),
            )
        )
    return sorted(out, key=lambda o: o.extra_clicks or 0, reverse=True)


# --- Page value (conversions import) -------------------------------------------------------

_PAGE_COLUMNS = ("page", "landing page", "landing_page", "page path", "url", "path")
_CONV_COLUMNS = ("conversions", "key events", "key_events", "leads", "lead count")


@dataclass
class PageValue:
    conversions: float
    clicks: float | None = None

    @property
    def per_click(self) -> float | None:
        return self.conversions / self.clicks if self.clicks else None


def load_conversions(text: str) -> dict[str, float]:
    """Page path -> conversions from a CSV export (GA4 landing-page report or a CRM
    export). Column names are matched case-insensitively; GA4's comment lines
    (``#``) and total rows are ignored."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    if not reader.fieldnames:
        return {}
    names = {n.strip().lower(): n for n in reader.fieldnames}
    page_col = next((names[c] for c in _PAGE_COLUMNS if c in names), None)
    conv_col = next((names[c] for c in _CONV_COLUMNS if c in names), None)
    if page_col is None or conv_col is None:
        raise ValueError(
            "the conversions file needs a page column (page / landing page / url) and "
            "a conversions column (conversions / key events / leads)"
        )
    out: dict[str, float] = defaultdict(float)
    for record in reader:
        raw_page = (record.get(page_col) or "").strip()
        if not raw_page or raw_page.lower() in ("total", "(not set)", "grand total"):
            continue
        try:
            value = float((record.get(conv_col) or "0").replace(",", ""))
        except ValueError:
            continue
        out[page_path(raw_page if "://" in raw_page else f"https://x{raw_page}")] += (
            value
        )
    return dict(out)


def page_values(
    conversions: dict[str, float], page_clicks: dict[str, float]
) -> dict[str, PageValue]:
    return {
        path: PageValue(conversions=value, clicks=page_clicks.get(path))
        for path, value in conversions.items()
    }


def add_leads(opportunities: list[Opportunity], values: dict[str, PageValue]) -> None:
    for opp in opportunities:
        value = values.get(opp.page)
        if value and value.per_click is not None and opp.extra_clicks is not None:
            opp.extra_leads = round(opp.extra_clicks * value.per_click, 2)


# --- Decay, pruning and merges ------------------------------------------------------------


@dataclass
class Window:
    clicks: float = 0.0
    impressions: float = 0.0


@dataclass
class PageTrend:
    page: str
    status: str  # growing | stable | decaying | dead | new
    decision: str  # keep | refresh | merge | prune-review
    recent: Window
    previous: Window
    peak: Window
    change_vs_previous_pct: float | None
    change_vs_peak_pct: float | None
    lost_queries: list[dict[str, object]] = field(default_factory=list)
    merge_with: str | None = None
    reason: str = ""


def _window(rows: list[GscRow], start: date, end: date) -> dict[str, Window]:
    out: dict[str, Window] = defaultdict(Window)
    for row in rows:
        if start <= row.day() <= end:
            w = out[page_path(row.get("page"))]
            w.clicks += row.clicks
            w.impressions += row.impressions
    return out


def cannibal_pairs(
    rows: list[GscRow], *, min_impressions: int = 50, min_share: float = 0.2
) -> dict[frozenset[str], list[str]]:
    """Page pairs that each take at least ``min_share`` of one query's impressions."""
    by_query: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        by_query[row.get("query")][page_path(row.get("page"))] += row.impressions
    pairs: dict[frozenset[str], list[str]] = defaultdict(list)
    for query, pages in by_query.items():
        total = sum(pages.values())
        if total < min_impressions:
            continue
        strong = sorted(p for p, v in pages.items() if v / total >= min_share)
        for i, a in enumerate(strong):
            for b in strong[i + 1 :]:
                pairs[frozenset((a, b))].append(query)
    return dict(pairs)


def page_trends(
    rows: list[GscRow],
    data_end: date,
    *,
    window_days: int = 28,
    lookback_days: int = 180,
    min_clicks: float = 10,
    known_pages: list[str] | None = None,
    queries_recent: list[GscRow] | None = None,
    queries_previous: list[GscRow] | None = None,
) -> list[PageTrend]:
    """``rows`` are date x page rows covering ``lookback_days`` up to ``data_end``."""
    recent_start = data_end - timedelta(days=window_days - 1)
    previous_end = recent_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=window_days - 1)
    recent = _window(rows, recent_start, data_end)
    previous = _window(rows, previous_start, previous_end)
    ninety = _window(rows, data_end - timedelta(days=89), data_end)
    lookback = _window(rows, data_end - timedelta(days=lookback_days - 1), data_end)

    peaks: dict[str, Window] = defaultdict(Window)
    start = data_end - timedelta(days=lookback_days - 1)
    while start + timedelta(days=window_days - 1) < recent_start:
        end = start + timedelta(days=window_days - 1)
        for page, w in _window(rows, start, end).items():
            if w.clicks > peaks[page].clicks:
                peaks[page] = w
        start += timedelta(days=window_days)

    pairs = cannibal_pairs(queries_recent or [])
    lost = _lost_queries(queries_previous or [], queries_recent or [])

    pages = set(recent) | set(previous) | set(peaks) | set(known_pages or [])
    out = []
    for page in sorted(pages):
        r, p, k = recent.get(page, Window()), previous.get(page, Window()), peaks[page]
        trend = PageTrend(
            page=page,
            status="stable",
            decision="keep",
            recent=r,
            previous=p,
            peak=k,
            change_vs_previous_pct=pct_change(p.clicks, r.clicks),
            change_vs_peak_pct=pct_change(k.clicks, r.clicks),
            lost_queries=lost.get(page, [])[:5],
        )
        if lookback.get(page, Window()).impressions == 0:
            # Never shown in the whole lookback: new, unindexed or not meant to rank.
            trend.status, trend.decision = "no-impressions", "index-check"
            trend.reason = f"No impressions in the last {lookback_days} days."
        elif ninety.get(page, Window()).impressions == 0:
            trend.status, trend.decision = "dead", "prune-review"
            trend.reason = "No impressions in the last 90 days."
        elif (
            p.clicks >= min_clicks
            and r.clicks <= p.clicks * 0.7
            and (k.clicks == 0 or r.clicks <= k.clicks * 0.6)
        ):
            trend.status, trend.decision = "decaying", "refresh"
            trend.reason = "Clicks fell 30%+ against the previous window and 40%+ against the page's best window."
            partner = _partner(page, pairs, recent)
            if partner:
                trend.decision, trend.merge_with = "merge", partner
                trend.reason += (
                    f" It shares queries with {partner}, which earns more clicks."
                )
        elif r.clicks >= min_clicks and r.clicks >= p.clicks * 1.3:
            trend.status = "growing"
        elif p.clicks == 0 and k.clicks == 0 and r.clicks > 0:
            trend.status = "new"
        out.append(trend)
    return out


def _partner(
    page: str, pairs: dict[frozenset[str], list[str]], recent: dict[str, Window]
) -> str | None:
    best = None
    for pair, queries in pairs.items():
        if page in pair and len(queries) >= 2:
            other = next(iter(pair - {page}))
            if recent.get(other, Window()).clicks > recent.get(page, Window()).clicks:
                if best is None or recent[other].clicks > recent[best].clicks:
                    best = other
    return best


def _lost_queries(
    before: list[GscRow], after: list[GscRow]
) -> dict[str, list[dict[str, object]]]:
    def totals(rows: list[GscRow]) -> dict[tuple[str, str], float]:
        out: dict[tuple[str, str], float] = defaultdict(float)
        for row in rows:
            out[(page_path(row.get("page")), row.get("query"))] += row.clicks
        return out

    b, a = totals(before), totals(after)
    losses: dict[str, list[dict[str, object]]] = defaultdict(list)
    for (page, query), clicks in b.items():
        drop = clicks - a.get((page, query), 0.0)
        if drop >= 3:
            losses[page].append(
                {"query": query, "clicks_before": clicks, "clicks_after": clicks - drop}
            )
    for items in losses.values():
        items.sort(
            key=lambda i: float(i["clicks_before"]) - float(i["clicks_after"]),
            reverse=True,
        )  # type: ignore[arg-type]
    return losses


# --- Findings ------------------------------------------------------------------------------


def _rank_priority(index: int) -> Priority:
    return "high" if index < 3 else "medium" if index < 10 else "low"


def opportunity_findings(
    opportunities: list[Opportunity], base_url: str, limit: int = 25
) -> list[Finding]:
    by_page: dict[tuple[str, str], list[Opportunity]] = defaultdict(list)
    for opp in opportunities:
        by_page[(opp.kind, opp.page)].append(opp)
    ranked = sorted(
        by_page.items(),
        key=lambda kv: (
            sum(o.extra_leads or 0 for o in kv[1]),
            sum(o.extra_clicks or 0 for o in kv[1]),
            sum(o.impressions for o in kv[1]),
        ),
        reverse=True,
    )[:limit]
    out = []
    for i, ((kind, page), opps) in enumerate(ranked):
        top = opps[:5]
        lines = "; ".join(
            f"'{o.query}' pos {o.position}, {int(o.impressions)} impr, CTR {o.ctr:.1%}"
            + (f", +{o.extra_clicks:.0f} clicks" if o.extra_clicks is not None else "")
            + (f", +{o.extra_leads:.2f} leads" if o.extra_leads else "")
            for o in top
        )
        url = base_url.rstrip("/") + page
        extra = sum(o.extra_clicks or 0 for o in opps)
        if kind == "striking-distance":
            out.append(
                finding(
                    key=f"striking-distance:{page}",
                    issue=f"{page} ranks 4-20 for {len(opps)} queries with real impressions",
                    category="performance/opportunity",
                    priority=_rank_priority(i),
                    evidence_items=[
                        evidence("Google Search Console (query x page)", lines, url)
                    ],
                    seo_impact=(
                        f"About {extra:.0f} extra clicks per period if these queries reached the "
                        "site's own top-3 CTR (estimate from the site's own data)."
                        if extra
                        else "Not estimated: too little data for the site's own top-3 CTR."
                    ),
                    action=(
                        "Strengthen this page for the listed queries: answer them in a heading "
                        "and the first paragraph, add internal links from related posts with the "
                        "query as anchor, and refresh outdated facts."
                    ),
                    outcome="Average position for these queries moves into the top 3.",
                    verification="Re-run /seo-opportunities after 28 days; compare position and clicks.",
                    skill=SKILL,
                    urls=[url],
                )
            )
        else:
            out.append(
                finding(
                    key=f"low-ctr:{page}",
                    issue=f"{page} ranks in the top 5 but its CTR is under half the site's own CTR there",
                    category="on-page/ctr",
                    priority=_rank_priority(i),
                    evidence_items=[
                        evidence("Google Search Console (query x page)", lines, url)
                    ],
                    seo_impact=f"About {extra:.0f} clicks per period are lost to the snippet.",
                    action=(
                        "Rewrite the title and meta description for the listed queries "
                        "(answer the query, show a concrete benefit, keep the length limits); "
                        "check whether a SERP feature or AI Overview takes the click."
                    ),
                    outcome="CTR reaches the site's own average at that position.",
                    verification="Compare CTR for the same queries 28 days after the change goes live.",
                    skill=SKILL,
                    urls=[url],
                )
            )
    return out


def trend_findings(trends: list[PageTrend], base_url: str) -> list[Finding]:
    out = []
    for t in trends:
        url = base_url.rstrip("/") + t.page
        numbers = (
            f"clicks last 28d {t.recent.clicks:.0f} (impr {t.recent.impressions:.0f}), "
            f"previous 28d {t.previous.clicks:.0f}, best 28d window {t.peak.clicks:.0f}"
        )
        if t.lost_queries:
            numbers += "; lost queries: " + ", ".join(
                f"'{q['query']}' {q['clicks_before']:.0f}->{q['clicks_after']:.0f}"  # type: ignore[str-format]
                for q in t.lost_queries
            )
        if t.status == "decaying":
            out.append(
                finding(
                    key=f"content-decay:{t.page}",
                    issue=f"{t.page} is losing clicks",
                    category="content/decay",
                    priority="high" if t.previous.clicks >= 50 else "medium",
                    evidence_items=[
                        evidence("Google Search Console (date x page)", numbers, url)
                    ],
                    seo_impact=t.reason,
                    action=(
                        f"Merge it into {t.merge_with} and 301-redirect it (owner decision)."
                        if t.decision == "merge"
                        else "Refresh it: update facts and dates, cover the lost queries, "
                        "improve the intro and internal links."
                    ),
                    outcome="Clicks return to at least the previous window's level.",
                    verification="Re-run /seo-opportunities 28 days after the update.",
                    skill=SKILL,
                    urls=[url],
                )
            )
        elif t.status == "no-impressions":
            out.append(
                finding(
                    key=f"no-impressions:{t.page}",
                    issue=f"{t.page} had no Google impressions in the whole lookback",
                    category="technical/indexing",
                    priority="medium",
                    evidence_items=[
                        evidence("Google Search Console (date x page)", t.reason, url)
                    ],
                    seo_impact="The page is either not indexed, too new, or not reachable for any query.",
                    action="Check its index status (URL Inspection, seo-reindex-agent), internal links to it, "
                    "and whether it is meant to rank; request indexing after fixing.",
                    outcome="The page earns impressions or is deliberately excluded.",
                    verification="Re-run /seo-opportunities after 28 days.",
                    skill=SKILL,
                    urls=[url],
                )
            )
        elif t.status == "dead":
            out.append(
                finding(
                    key=f"prune-review:{t.page}",
                    issue=f"{t.page} had no Google impressions in 90 days",
                    category="content/decay",
                    priority="low",
                    evidence_items=[
                        evidence("Google Search Console (date x page)", numbers, url)
                    ],
                    seo_impact="The page earns no search visibility and may dilute crawl attention.",
                    action=(
                        "Review it (owner decision): improve and reindex, merge into a stronger "
                        "page with a redirect, or noindex it. Check backlinks and conversions "
                        "first; never delete a page that converts."
                    ),
                    outcome="Every indexable page either earns impressions or is consolidated.",
                    verification="Impressions appear within 28 days of the change, or the URL is redirected.",
                    skill=SKILL,
                    urls=[url],
                )
            )
    return out


def snapshot(
    opportunities: list[Opportunity], trends: list[PageTrend], curve: dict[int, float]
) -> dict[str, object]:
    return {
        "ctr_curve": {str(k): round(v, 4) for k, v in curve.items()},
        "opportunities": [asdict(o) for o in opportunities[:100]],
        "trends": [
            {
                **{
                    k: v
                    for k, v in asdict(t).items()
                    if k not in ("recent", "previous", "peak")
                },
                "recent": asdict(t.recent),
                "previous": asdict(t.previous),
                "peak": asdict(t.peak),
            }
            for t in trends
            if t.status != "stable"
        ],
    }


def read_conversions_file(path: Path) -> dict[str, float]:
    return load_conversions(path.read_text(encoding="utf-8-sig"))
