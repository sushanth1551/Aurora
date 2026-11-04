"""Early warnings from Search Console: drops that are real, not noise (pure functions).

A week is compared with the eight weeks before it using a *robust* z-score
(median and median absolute deviation), so one unusual week in the baseline does
not hide or trigger an alert. An alert needs all three: a robust outlier, a drop
of at least ``min_drop_pct`` against the median, and enough baseline volume.
Index status of key pages comes from URL Inspection (read-only).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from app.seo.intelligence.insights.common import (
    GscRow,
    evidence,
    finding,
    page_path,
    slug,
)
from app.seo.intelligence.models import AlertSettings, Finding, Priority

SKILL = "seo-alerts"
BASELINE_WEEKS = 8
MAD_SCALE = 1.4826  # makes the MAD comparable to a standard deviation
POSITION_DROP = 3.0
INDEXED_STATES = ("Submitted and indexed", "Indexed, not submitted in sitemap")


@dataclass
class Alert:
    kind: str  # site-clicks | site-impressions | page-clicks | query-position | index-status
    subject: str
    current: float | None
    baseline: float | None
    change_pct: float | None
    robust_z: float | None
    detail: str
    priority: str


def weekly(
    rows: list[GscRow], data_end: date, key: str | None = None, metric: str = "clicks"
) -> dict[str, list[float]]:
    """Per subject (``key`` dimension, or the whole site), 9 weekly sums ending
    ``data_end``: index 0 is the oldest baseline week, index 8 the latest week."""
    out: dict[str, list[float]] = defaultdict(lambda: [0.0] * (BASELINE_WEEKS + 1))
    first = data_end - timedelta(days=7 * (BASELINE_WEEKS + 1) - 1)
    for row in rows:
        day = row.day()
        if not first <= day <= data_end:
            continue
        week = (day - first).days // 7
        subject = (
            "site"
            if key is None
            else page_path(row.get(key))
            if key == "page"
            else row.get(key)
        )
        out[subject][week] += getattr(row, metric)
    return dict(out)


def robust_drop(series: list[float]) -> tuple[float, float | None, float | None]:
    """(baseline median, change %, robust z) of the last value against the rest."""
    baseline, current = series[:-1], series[-1]
    median = statistics.median(baseline)
    mad = statistics.median(abs(v - median) for v in baseline)
    change = None if median == 0 else (current - median) / median * 100
    z = None if mad == 0 else (current - median) / (MAD_SCALE * mad)
    return median, change, z


def _is_drop(
    median: float,
    change: float | None,
    z: float | None,
    settings: AlertSettings,
    floor: float,
) -> bool:
    if median < floor or change is None or change > -settings.min_drop_pct:
        return False
    # A flat baseline (MAD 0) has no z-score: the percentage drop alone decides.
    return z is None or z <= -settings.robust_z


def detect(
    site_daily: list[GscRow],
    page_daily: list[GscRow],
    query_daily: list[GscRow],
    data_end: date,
    settings: AlertSettings,
    *,
    max_page_alerts: int = 10,
) -> list[Alert]:
    alerts: list[Alert] = []
    for metric, floor in (
        ("clicks", settings.min_weekly_clicks),
        ("impressions", settings.min_weekly_clicks * 20),
    ):
        series = weekly(site_daily, data_end, metric=metric).get("site")
        if series:
            median, change, z = robust_drop(series)
            if _is_drop(median, change, z, settings, floor):
                alerts.append(
                    Alert(
                        kind=f"site-{metric}",
                        subject="whole site",
                        current=series[-1],
                        baseline=median,
                        change_pct=round(change, 1) if change is not None else None,
                        robust_z=round(z, 2) if z is not None else None,
                        detail=f"Last 7 days {series[-1]:.0f} {metric} vs median {median:.0f} of the 8 weeks before.",
                        priority="critical" if (change or 0) <= -50 else "high",
                    )
                )
    page_alerts = []
    for page, series in weekly(page_daily, data_end, key="page").items():
        median, change, z = robust_drop(series)
        if _is_drop(median, change, z, settings, settings.min_weekly_clicks / 2):
            page_alerts.append(
                Alert(
                    kind="page-clicks",
                    subject=page,
                    current=series[-1],
                    baseline=median,
                    change_pct=round(change, 1) if change is not None else None,
                    robust_z=round(z, 2) if z is not None else None,
                    detail=f"Last 7 days {series[-1]:.0f} clicks vs median {median:.0f}.",
                    priority="high"
                    if median >= settings.min_weekly_clicks
                    else "medium",
                )
            )
    page_alerts.sort(key=lambda a: (a.baseline or 0) - (a.current or 0), reverse=True)
    alerts += page_alerts[:max_page_alerts]
    alerts += position_drops(query_daily, data_end)
    return alerts


def position_drops(
    query_daily: list[GscRow],
    data_end: date,
    *,
    min_impressions: float = 100,
    limit: int = 10,
) -> list[Alert]:
    """Queries whose weighted position in the last 7 days is 3+ places worse than
    the 8 weeks before, with enough impressions in both periods."""
    split = data_end - timedelta(days=6)
    first = data_end - timedelta(days=7 * (BASELINE_WEEKS + 1) - 1)
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for row in query_daily:
        day = row.day()
        if not first <= day <= data_end:
            continue
        a = acc[row.get("query")]
        i = 2 if day >= split else 0
        a[i] += row.position * row.impressions
        a[i + 1] += row.impressions
    out = []
    for query, (bw, bi, rw, ri) in acc.items():
        if bi < min_impressions or ri < min_impressions / 8:
            continue
        before, after = bw / bi, rw / ri
        if after - before >= POSITION_DROP:
            out.append(
                Alert(
                    kind="query-position",
                    subject=query,
                    current=round(after, 1),
                    baseline=round(before, 1),
                    change_pct=None,
                    robust_z=None,
                    detail=f"Average position {before:.1f} -> {after:.1f} ({ri:.0f} impressions last 7 days).",
                    priority="medium",
                )
            )
    out.sort(key=lambda a: (a.current or 0) - (a.baseline or 0), reverse=True)
    return out[:limit]


def index_alerts(inspections: dict[str, dict[str, Any]]) -> list[Alert]:
    """Key pages whose URL Inspection result is not 'indexed'."""
    out = []
    for url, status in inspections.items():
        if not status:
            continue
        coverage = str(status.get("coverageState") or "")
        verdict = str(status.get("verdict") or "")
        canonical_mismatch = (
            status.get("googleCanonical")
            and status.get("userCanonical")
            and status["googleCanonical"] != status["userCanonical"]
        )
        if (
            coverage in INDEXED_STATES
            and verdict in ("PASS", "")
            and not canonical_mismatch
        ):
            continue
        detail = f"verdict {verdict or 'n/a'}, coverage '{coverage or 'n/a'}'"
        if canonical_mismatch:
            detail += f", Google chose canonical {status.get('googleCanonical')}"
        out.append(
            Alert(
                kind="index-status",
                subject=url,
                current=None,
                baseline=None,
                change_pct=None,
                robust_z=None,
                detail=detail,
                priority="critical" if verdict == "FAIL" else "high",
            )
        )
    return out


def alert_key(a: Alert) -> str:
    """``<check>:<scope>`` keys (scope: ``site``, a path or a query slug)."""
    if a.kind == "site-clicks":
        return "traffic-drop:site"
    if a.kind == "site-impressions":
        return "impressions-drop:site"
    if a.kind == "page-clicks":
        return f"page-clicks-drop:{a.subject}"
    if a.kind == "index-status":
        return f"index-status:{page_path(a.subject)}"
    return f"query-position-drop:{slug(a.subject)}"


def alert_findings(alerts: list[Alert], base_url: str) -> list[Finding]:
    out = []
    for a in alerts:
        url = (
            a.subject
            if a.subject.startswith("http")
            else base_url.rstrip("/") + a.subject
            if a.subject.startswith("/")
            else None
        )
        actions = {
            "site-clicks": "Check Search Console for a manual action, a Google update date or a "
            "deploy around the drop; compare top pages and queries; run /seo-monitor.",
            "site-impressions": "Check indexing (Pages report), robots.txt and recent deploys; "
            "compare with known Google updates before changing content.",
            "page-clicks": "Inspect the page live (title, status, canonical, indexing) and its "
            "top queries; check whether a competitor or AI Overview took the result.",
            "query-position": "Look at the live results for this query and at the page that ranks; "
            "check for a newer competitor page or lost internal links.",
            "index-status": "Fix the reason shown (canonical, noindex, fetch error) and request "
            "indexing through the seo-reindex-agent after the fix is live.",
        }
        out.append(
            finding(
                key=alert_key(a),
                issue=f"{a.kind.replace('-', ' ')}: {a.subject}",
                category="technical/indexing"
                if a.kind == "index-status"
                else "performance/alert",
                priority=a.priority,  # type: ignore[arg-type]
                evidence_items=[
                    evidence(
                        "Google URL Inspection"
                        if a.kind == "index-status"
                        else "Google Search Console",
                        a.detail
                        + (
                            f" (robust z {a.robust_z})"
                            if a.robust_z is not None
                            else ""
                        ),
                        url,
                    )
                ],
                seo_impact="Visible loss of search traffic or indexing if it persists.",
                action=actions[a.kind],
                outcome="The metric returns to its baseline range.",
                verification="The next weekly /seo-alerts run no longer raises this alert.",
                skill=SKILL,
                urls=[url] if url else [],
            )
        )
    return out


def priority_of(alerts: list[Alert]) -> Priority | None:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return min((a.priority for a in alerts), key=lambda p: order[p], default=None)  # type: ignore[return-value]


def snapshot(alerts: list[Alert]) -> list[dict[str, Any]]:
    return [asdict(a) for a in alerts]
