"""Shared helpers for the insight modules: Search Console rows and finding builders."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

from app.seo.intelligence.models import Evidence, Finding, Priority, utcnow

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class GscRow:
    """One Search Analytics row with its dimension values by name."""

    keys: dict[str, str]
    clicks: float
    impressions: float
    position: float

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    def get(self, name: str) -> str:
        return self.keys.get(name, "")

    def day(self) -> date:
        return date.fromisoformat(self.keys["date"])


def rows_of(raw: list[dict[str, object]], dimensions: tuple[str, ...]) -> list[GscRow]:
    """Search Console rows (``keys`` aligned with ``dimensions``) as ``GscRow``s;
    malformed rows are skipped, never repaired."""
    out: list[GscRow] = []
    for item in raw:
        keys = item.get("keys")
        if not isinstance(keys, list) or len(keys) != len(dimensions):
            continue
        try:
            out.append(
                GscRow(
                    keys={d: str(k) for d, k in zip(dimensions, keys, strict=True)},
                    clicks=float(item.get("clicks") or 0),  # type: ignore[arg-type]
                    impressions=float(item.get("impressions") or 0),  # type: ignore[arg-type]
                    position=float(item.get("position") or 0),  # type: ignore[arg-type]
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def page_path(url: str) -> str:
    path = urlparse(url).path or "/"
    return path.rstrip("/") or "/"


def slug(text: str, limit: int = 80) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")[:limit] or "x"


def weighted_position(rows: list[GscRow]) -> float | None:
    impressions = sum(r.impressions for r in rows)
    if not impressions:
        return None
    return sum(r.position * r.impressions for r in rows) / impressions


def evidence(source: str, observation: str, url: str | None = None) -> Evidence:
    return Evidence(
        source=source, observation=observation[:2000], url=url, observed_at=utcnow()
    )


def finding(
    *,
    key: str,
    issue: str,
    category: str,
    priority: Priority,
    evidence_items: list[Evidence],
    seo_impact: str,
    action: str,
    outcome: str,
    verification: str,
    skill: str,
    urls: list[str] | None = None,
) -> Finding:
    return Finding(
        key=key[:240],
        issue=issue,
        category=category,
        priority=priority,
        evidence=evidence_items,
        affected_urls=urls or [],
        seo_impact=seo_impact,
        recommended_action=action,
        expected_outcome=outcome,
        verification_method=verification,
        skill=skill,
    )


def pct_change(before: float, after: float) -> float | None:
    return None if before == 0 else (after - before) / before * 100.0


PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
BLOG_PREFIX = "/blog/"


def blog_scoped(key: str, checks: tuple[str, ...]) -> str:
    """``<check>:/blog/<post>`` -> ``<check>:blog`` for the listed checks (the
    ledger's convention for blog-wide issues such as ``long-titles:blog``)."""
    check, _, scope = key.partition(":")
    if check in checks and scope.startswith(BLOG_PREFIX):
        return f"{check}:blog"
    return key


def aggregate_blog(
    findings: list[Finding], checks: tuple[str, ...], labels: dict[str, str]
) -> list[Finding]:
    """Merge per-post findings of ``checks`` into one ``<check>:blog`` finding that
    lists every affected post (evidence capped at 15 entries)."""
    out: list[Finding] = []
    groups: dict[str, list[Finding]] = {}
    for f in findings:
        key = blog_scoped(f.key, checks)
        if key == f.key:
            out.append(f)
        else:
            groups.setdefault(key, []).append(f)
    for key, items in groups.items():
        first = items[0]
        check = key.split(":", 1)[0]
        merged = first.model_copy(deep=True)
        merged.key = key
        merged.issue = f"{labels.get(check, check)} on {len(items)} blog post(s)"
        merged.priority = min(
            (f.priority for f in items), key=lambda p: PRIORITY_RANK[p]
        )
        merged.affected_urls = [u for f in items for u in f.affected_urls][:200]
        merged.evidence = [
            evidence(
                e.source,
                f"{page_path(f.affected_urls[0]) if f.affected_urls else ''}: {e.observation}"[
                    :600
                ],
                e.url,
            )
            for f in items
            for e in f.evidence[:1]
        ][:15]
        out.append(merged)
    return out
