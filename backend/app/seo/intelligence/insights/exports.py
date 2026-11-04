"""Exports and the dashboard summary.

CSV files (UTF-8 with BOM, so Excel opens them correctly) for open findings,
opportunities, alerts, market ranks and competitor changes, and one dashboard
dictionary that the API serves to the AURORA dashboard. Everything is read from
the knowledge base; nothing is recomputed or estimated here.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from app.seo.intelligence.insights.store import KINDS, InsightStore
from app.seo.intelligence.models import ACTIVE_STATUSES, PRIORITY_ORDER, Finding


def _csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO()
    fields = list(dict.fromkeys(k for r in rows for k in r))
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(
            {
                k: ("; ".join(map(str, v)) if isinstance(v, list) else v)
                for k, v in r.items()
            }
        )
    return "﻿" + buffer.getvalue()


def finding_rows(findings: list[Finding]) -> list[dict[str, Any]]:
    active = [f for f in findings if f.status in ACTIVE_STATUSES]
    active.sort(key=lambda f: (PRIORITY_ORDER[f.priority], f.category, f.key))
    return [
        {
            "priority": f.priority,
            "category": f.category,
            "issue": f.issue,
            "status": f.status,
            "recommended_action": f.recommended_action,
            "affected_urls": f.affected_urls[:20],
            "first_seen": f.first_seen,
            "last_seen": f.last_seen,
            "key": f.key,
            "skill": f.skill,
        }
        for f in active
    ]


def export_csv(
    store: InsightStore, site_id: str, findings: list[Finding]
) -> list[Path]:
    out_dir = store.data_dir(site_id) / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict[str, Any]]] = {"findings": finding_rows(findings)}
    opp = store.latest(site_id, "opportunities")
    if opp:
        tables["opportunities"] = list(opp["data"].get("opportunities", []))
        tables["content-trends"] = list(opp["data"].get("trends", []))
    alerts = store.latest(site_id, "alerts")
    if alerts:
        tables["alerts"] = list(alerts["data"].get("alerts", []))
    ranks = store.latest(site_id, "rank-tracking")
    if ranks:
        tables["market-ranks"] = list(ranks["data"].get("market_ranks", []))
        tables["serp-checks"] = list(ranks["data"].get("serp_checks", []))
    watch = store.latest(site_id, "competitor-watch")
    if watch:
        tables["competitor-changes"] = [
            {
                "domain": c["domain"],
                "new": c.get("new_count"),
                "updated": c.get("updated_count"),
                "removed": c.get("removed_count"),
                "uncovered_topics": c.get("uncovered", []),
            }
            for c in watch["data"].get("changes", [])
        ]
    paths = []
    for name, rows in tables.items():
        if rows:
            path = out_dir / f"{site_id}-{name}.csv"
            path.write_text(_csv(rows), encoding="utf-8")
            paths.append(path)
    return paths


def dashboard(
    store: InsightStore, site_id: str, findings: list[Finding]
) -> dict[str, Any]:
    active = [f for f in findings if f.status in ACTIVE_STATUSES]
    by_priority: dict[str, int] = {p: 0 for p in PRIORITY_ORDER}
    by_category: dict[str, int] = {}
    for f in active:
        by_priority[f.priority] += 1
        by_category[f.category] = by_category.get(f.category, 0) + 1
    latest = {kind: store.latest(site_id, kind) for kind in KINDS}

    def data(kind: str, key: str, limit: int = 15) -> list[Any]:
        snap = latest.get(kind)
        value = snap["data"].get(key, []) if snap else []
        return list(value)[:limit] if isinstance(value, list) else value

    return {
        "site_id": site_id,
        "open_findings": {
            "total": len(active),
            "by_priority": by_priority,
            "by_category": by_category,
        },
        "top_findings": finding_rows(active)[:15],
        "updated": {kind: (snap or {}).get("date") for kind, snap in latest.items()},
        "opportunities": data("opportunities", "opportunities"),
        "content_trends": data("opportunities", "trends"),
        "alerts": data("alerts", "alerts", 20),
        "market_ranks": data("rank-tracking", "market_ranks", 50),
        "serp_checks": data("rank-tracking", "serp_checks", 30),
        "ai_crawlers": data("ai-visibility", "crawler_access", 30),
        "ai_share_of_voice": (latest.get("ai-visibility") or {})
        .get("data", {})
        .get("share_of_voice"),
        "competitor_changes": data("competitor-watch", "changes", 20),
        "crawl": {
            k: v
            for k, v in ((latest.get("site-crawl") or {}).get("data") or {}).items()
            if k in ("coverage", "usage", "rule_checks", "soft_404_status")
        },
    }
