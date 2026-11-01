"""Detectors over stored Search Console aggregates (pure and deterministic).

Method: ``seo-toolkit-performance-analysis`` and ``seo-gsc-diagnosis``: describe what
changed with the numbers that show it, keep causes labelled as hypotheses, never
treat missing data as zero, and state when history is too short to compare.

No benchmark is invented: "low CTR" is measured against the property's **own**
CTR at the same average position. A detector returns nothing (and says why) when
its inputs are too thin.
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.monitoring.models import Agg, Thresholds

BUCKETS = ("1", "2", "3", "4-5", "6-10", "11-20", "21+")


def bucket(position: float) -> str:
    if position < 1.5:
        return "1"
    if position < 2.5:
        return "2"
    if position < 3.5:
        return "3"
    if position < 5.5:
        return "4-5"
    if position < 10.5:
        return "6-10"
    if position < 20.5:
        return "11-20"
    return "21+"


def pct_change(current: float, previous: float) -> float | None:
    """Relative change, or ``None`` when there is no previous value to compare with."""
    return (current - previous) / previous if previous else None


def r(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


# -- CTR baselines and low-CTR detection ---------------------------------------------


@dataclass(frozen=True)
class Baseline:
    bucket: str
    ctr: float
    rows: int
    impressions: float


def ctr_baselines(
    rows: Sequence[Agg], *, min_impressions: int = 10, min_rows: int = 20
) -> dict[str, Baseline]:
    """Impression-weighted CTR per position bucket from the property's own rows.

    A bucket is returned only when enough rows support it; otherwise there is no
    baseline for it and nothing in that bucket can be called "low".
    """
    clicks: dict[str, float] = defaultdict(float)
    impressions: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.impressions < min_impressions or row.position is None:
            continue
        b = bucket(row.position)
        clicks[b] += row.clicks
        impressions[b] += row.impressions
        counts[b] += 1
    return {
        b: Baseline(b, clicks[b] / impressions[b], counts[b], impressions[b])
        for b in counts
        if counts[b] >= min_rows and impressions[b] > 0
    }


def find_low_ctr(
    rows: Sequence[Agg],
    baselines: dict[str, Baseline],
    t: Thresholds,
    *,
    leave_one_out: bool = False,
) -> list[dict[str, Any]]:
    """Rows whose CTR is far below the property's own CTR at the same position.

    With ``leave_one_out`` (used when ``baselines`` came from these same rows) the
    row being judged is removed from its own baseline, so a badly underperforming
    page cannot drag down the yardstick it is measured against.
    """
    found: list[dict[str, Any]] = []
    for row in rows:
        position = row.position
        if row.impressions < t.min_impressions or position is None or position > 20.5:
            continue
        base = baselines.get(bucket(position))
        if base is None:
            continue  # no own-data baseline for this position range
        ref_ctr, ref_rows = base.ctr, base.rows
        if leave_one_out:
            ref_impressions = base.impressions - row.impressions
            ref_rows = base.rows - 1
            if ref_impressions <= 0 or ref_rows < t.min_baseline_rows:
                continue  # not enough other rows to judge this one fairly
            ref_ctr = (base.ctr * base.impressions - row.clicks) / ref_impressions
        expected = row.impressions * ref_ctr
        shortfall = expected - row.clicks
        if (
            row.ctr < t.low_ctr_ratio * ref_ctr
            and shortfall >= t.min_ctr_shortfall_clicks
        ):
            found.append(
                {
                    "page": row.page,
                    "query": row.query,
                    "impressions": row.impressions,
                    "clicks": row.clicks,
                    "ctr": r(row.ctr),
                    "position": r(position, 2),
                    "baseline_ctr": r(ref_ctr),
                    "baseline_bucket": base.bucket,
                    "baseline_rows": ref_rows,
                    "expected_clicks": r(expected, 1),
                    "shortfall_clicks": r(shortfall, 1),
                }
            )
    found.sort(
        key=lambda f: (-f["shortfall_clicks"], f["page"] or "", f["query"] or "")
    )
    return found[: t.max_findings_per_kind]


# -- declines -------------------------------------------------------------------------------


def _material_drop(current: float, previous: float, t: Thresholds) -> bool:
    """A relative drop that also exceeds count noise (about two Poisson standard deviations)."""
    if previous < t.min_previous_clicks:
        return False
    drop = previous - current
    return drop / previous >= t.decline_ratio and drop >= max(
        10.0, 2 * math.sqrt(previous)
    )


def decline_driver(cur: Agg, prev: Agg) -> tuple[str, list[str]]:
    """Which part of clicks = impressions x CTR moved, and what it could mean (hypotheses)."""
    imp = pct_change(cur.impressions, prev.impressions)
    ctr = pct_change(cur.ctr, prev.ctr)
    pos_c, pos_p = cur.position, prev.position
    delta = (pos_c - pos_p) if pos_c is not None and pos_p is not None else None
    if delta is not None and delta >= 1.5 and imp is not None and imp <= -0.1:
        return "ranking_loss", [
            "The site appears lower for these searches (average position worse and impressions down).",
            "Check the live results for the main queries for new or stronger competitors.",
            "Check whether the page changed recently or lost internal links.",
        ]
    if imp is not None and imp <= -0.2 and (delta is None or abs(delta) < 1.0):
        return "impressions_drop", [
            "Fewer impressions at a similar position: lower search demand (seasonality) or "
            "reduced visibility for some queries.",
            "Compare with the same period last year if the property has that history.",
            "If impressions fell to zero, check that the page is still indexed (URL Inspection, not integrated).",
        ]
    if ctr is not None and ctr <= -0.2 and (imp is None or imp > -0.1):
        return "ctr_drop", [
            "Same visibility but fewer clicks: the snippet or the results page may have changed "
            "(new SERP features, AI answers, competitors' titles).",
            "Check the live result for the main queries.",
        ]
    return "mixed", [
        "Several factors moved together; inspect the top queries of this page."
    ]


def find_declining_clicks(
    cur: dict[str, Agg], prev: dict[str, Agg], t: Thresholds
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for page, before in prev.items():
        now = cur.get(page, Agg(page=page))
        if not _material_drop(now.clicks, before.clicks, t):
            continue
        driver, hypotheses = decline_driver(now, before)
        found.append(
            {
                "page": page,
                "clicks_current": now.clicks,
                "clicks_previous": before.clicks,
                "clicks_change": r(pct_change(now.clicks, before.clicks)),
                "lost_clicks": before.clicks - now.clicks,
                "impressions_current": now.impressions,
                "impressions_previous": before.impressions,
                "impressions_change": r(
                    pct_change(now.impressions, before.impressions)
                ),
                "ctr_current": r(now.ctr),
                "ctr_previous": r(before.ctr),
                "position_current": r(now.position, 2),
                "position_previous": r(before.position, 2),
                "driver": driver,
                "hypotheses": hypotheses,
            }
        )
    found.sort(key=lambda f: (-f["lost_clicks"], f["page"]))
    return found[: t.max_findings_per_kind]


def find_position_decline(
    cur: dict[str, Agg], prev: dict[str, Agg], t: Thresholds
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for page, before in prev.items():
        now = cur.get(page)
        if (
            now is None
            or before.impressions < t.min_impressions
            or now.impressions < t.min_impressions
        ):
            continue
        if now.position is None or before.position is None:
            continue
        delta = now.position - before.position
        if delta >= t.position_worsening:
            found.append(
                {
                    "page": page,
                    "position_current": r(now.position, 2),
                    "position_previous": r(before.position, 2),
                    "position_change": r(delta, 2),
                    "impressions_current": now.impressions,
                    "impressions_previous": before.impressions,
                    "clicks_current": now.clicks,
                    "clicks_previous": before.clicks,
                }
            )
    found.sort(key=lambda f: (-f["position_change"], f["page"]))
    return found[: t.max_findings_per_kind]


def find_growing(
    cur: dict[str, Agg], prev: dict[str, Agg], t: Thresholds
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for page, now in cur.items():
        before = prev.get(page)
        if before is None or before.clicks < t.min_previous_clicks:
            continue
        gain = now.clicks - before.clicks
        if gain / before.clicks >= t.decline_ratio and gain >= max(
            10.0, 2 * math.sqrt(before.clicks)
        ):
            found.append(
                {
                    "page": page,
                    "clicks_current": now.clicks,
                    "clicks_previous": before.clicks,
                    "clicks_change": r(pct_change(now.clicks, before.clicks)),
                    "gained_clicks": gain,
                    "impressions_current": now.impressions,
                    "impressions_previous": before.impressions,
                    "position_current": r(now.position, 2),
                    "position_previous": r(before.position, 2),
                }
            )
    found.sort(key=lambda f: (-f["gained_clicks"], f["page"]))
    return found[: t.max_findings_per_kind]


# -- new queries ----------------------------------------------------------------------------------


def find_new_queries(
    cur_query_pages: Sequence[Agg], seen_before: set[str], t: Thresholds
) -> list[dict[str, Any]]:
    """Queries with impressions now that never appeared in earlier stored data."""
    by_query: dict[str, list[Agg]] = defaultdict(list)
    for row in cur_query_pages:
        if row.query:
            by_query[row.query].append(row)
    found: list[dict[str, Any]] = []
    for query, rows in by_query.items():
        impressions = sum(x.impressions for x in rows)
        if query in seen_before or impressions < t.new_query_min_impressions:
            continue
        clicks = sum(x.clicks for x in rows)
        top = max(rows, key=lambda x: (x.impressions, x.page or ""))
        weighted = sum(x.position_weighted for x in rows)
        found.append(
            {
                "query": query,
                "impressions": impressions,
                "clicks": clicks,
                "ctr": r(clicks / impressions if impressions else 0.0),
                "position": r(weighted / impressions if impressions else None, 2),
                "top_page": top.page,
                "pages": len(rows),
            }
        )
    found.sort(key=lambda f: (-f["impressions"], f["query"]))
    return found[: t.max_findings_per_kind]


# -- cannibalization ------------------------------------------------------------------------------


def find_cannibalization(
    cur_query_pages: Sequence[Agg], t: Thresholds
) -> list[dict[str, Any]]:
    """Queries for which several of the site's pages each get a meaningful share.

    Google may legitimately show more than one URL, so this is only a *potential*
    problem. Shared queries alone are not enough: every counted page needs both a
    minimum number of impressions and a minimum share of the query's impressions.
    """
    by_query: dict[str, list[Agg]] = defaultdict(list)
    for row in cur_query_pages:
        if row.query and row.page:
            by_query[row.query].append(row)
    found: list[dict[str, Any]] = []
    for query, rows in by_query.items():
        total = sum(x.impressions for x in rows)
        if len(rows) < 2 or total < t.cannibal_min_impressions:
            continue
        significant = [
            x
            for x in rows
            if x.impressions / total >= t.cannibal_min_share
            and (x.position or 99) <= 20.5
        ]
        if len(significant) < 2:
            continue
        significant.sort(key=lambda x: (-x.impressions, x.page or ""))
        found.append(
            {
                "query": query,
                "total_impressions": total,
                "total_clicks": sum(x.clicks for x in rows),
                "pages": [
                    {
                        "page": x.page,
                        "impressions": x.impressions,
                        "clicks": x.clicks,
                        "share": r(x.impressions / total),
                        "position": r(x.position, 2),
                    }
                    for x in significant
                ],
                "other_pages": len(rows) - len(significant),
            }
        )
    found.sort(key=lambda f: (-f["total_impressions"], f["query"]))
    return found[: t.max_findings_per_kind]


# -- new articles -----------------------------------------------------------------------------------


def article_data_state(
    published: date | None, today: date, pages_with_rows: bool
) -> dict[str, Any]:
    """Whether a mapped page has any stored performance, with the honest reason when not."""
    days = (today - published).days if published else None
    if pages_with_rows:
        return {"state": "has_data", "days_since_publish": days}
    if days is None:
        return {
            "state": "no_data",
            "days_since_publish": None,
            "note": "No stored Search Console rows for this page and no publish date recorded.",
        }
    if days < 0:
        return {
            "state": "not_published_yet",
            "days_since_publish": days,
            "note": "The recorded publish date is in the future.",
        }
    if days <= 14:
        return {
            "state": "too_new",
            "days_since_publish": days,
            "note": "Search Console data usually appears days after a page is indexed and has a "
            "2-3 day reporting lag. No data yet is expected, not a problem.",
        }
    return {
        "state": "no_data",
        "days_since_publish": days,
        "note": "No impressions are stored for this page after more than two weeks. AURORA cannot "
        "tell whether it is indexed (URL Inspection is not integrated); check in Search "
        "Console, and re-sync in case the data has not been fetched yet.",
    }
