"""Keyword demand per market: free Search Console data + optional DataForSEO data.

Search Console shows real demand *for queries the site already appears for*
(impressions per market). DataForSEO adds search volume, CPC and difficulty for
any keyword, plus backlink counts, when the owner configured it and set a monthly
USD budget. The budget is checked against the cost DataForSEO itself reports
after each request; AURORA never estimates a price.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Protocol

from app.seo.intelligence.insights.common import GscRow, evidence, finding, slug
from app.seo.intelligence.insights.market_ranks import ISO2_TO_GSC, normalize
from app.seo.intelligence.models import Finding, KeywordDataSettings, WebsiteConfig

SKILL = "seo-rank-tracking"
MIN_GAP_VOLUME = 100


@dataclass
class KeywordRow:
    keyword: str
    market: str
    gsc_impressions: float = 0.0
    gsc_clicks: float = 0.0
    gsc_position: float | None = None
    search_volume: int | None = None
    keyword_difficulty: int | None = None
    cpc: float | None = None


def candidates(
    config: WebsiteConfig, rows: list[GscRow], top_queries: int = 40
) -> list[str]:
    """Tracked + seed keywords first, then the site's top Search Console queries."""
    seen: dict[str, str] = {}
    for k in config.rank_tracking.keywords + config.seed_keywords:
        seen.setdefault(normalize(k), k)
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        totals[row.get("query")] += row.impressions
    for query, _ in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[
        :top_queries
    ]:
        seen.setdefault(normalize(query), query)
    return list(seen.values())


def gsc_demand(
    rows: list[GscRow], keywords: list[str], markets: list[str]
) -> list[KeywordRow]:
    """``rows`` are ``query x country`` rows."""
    wanted = {normalize(k): k for k in keywords}
    gsc_to_iso = {ISO2_TO_GSC.get(m, ""): m for m in markets}
    acc: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for row in rows:
        k = wanted.get(normalize(row.get("query")))
        m = gsc_to_iso.get(row.get("country").lower())
        if k is None or m is None:
            continue
        a = acc[(k, m)]
        a[0] += row.impressions
        a[1] += row.clicks
        a[2] += row.position * row.impressions
    out = []
    for k in keywords:
        for m in markets:
            impr, clicks, weighted = acc.get((k, m), [0.0, 0.0, 0.0])
            out.append(
                KeywordRow(
                    keyword=k,
                    market=m,
                    gsc_impressions=impr,
                    gsc_clicks=clicks,
                    gsc_position=round(weighted / impr, 1) if impr else None,
                )
            )
    return out


class KeywordProvider(Protocol):
    cost_usd: float

    async def search_volume(
        self, keywords: list[str], location_code: int, language_code: str = "en"
    ) -> list[dict[str, Any]]: ...

    async def keyword_difficulty(
        self, keywords: list[str], location_code: int, language_code: str = "en"
    ) -> list[dict[str, Any]]: ...


def month_spent(usage: dict[str, float], today: date) -> float:
    return float(usage.get(today.strftime("%Y-%m"), 0.0))


async def enrich(
    provider: KeywordProvider,
    rows: list[KeywordRow],
    settings: KeywordDataSettings,
    language: str,
    spent_this_month: float,
) -> tuple[float, list[str]]:
    """Adds volume, CPC and difficulty per market while the budget lasts.
    Returns (USD spent in this run as reported by the provider, notes)."""
    notes: list[str] = []
    start_cost = provider.cost_usd
    by_market: dict[str, list[KeywordRow]] = defaultdict(list)
    for r in rows:
        by_market[r.market].append(r)
    for market, items in by_market.items():
        code = settings.location_codes.get(market)
        if code is None:
            notes.append(f"No DataForSEO location code configured for {market}.")
            continue
        keywords = sorted({r.keyword for r in items})
        for step in ("volume", "difficulty"):
            if (
                spent_this_month + (provider.cost_usd - start_cost)
                >= settings.monthly_budget_usd
            ):
                notes.append(
                    f"Monthly DataForSEO budget reached before {step} for {market}."
                )
                break
            try:
                if step == "volume":
                    data = await provider.search_volume(keywords, code, language)
                    values = {normalize(str(d.get("keyword"))): d for d in data}
                    for r in items:
                        d = values.get(normalize(r.keyword))
                        if d:
                            r.search_volume = d.get("search_volume")
                            r.cpc = d.get("cpc")
                else:
                    data = await provider.keyword_difficulty(keywords, code, language)
                    values = {normalize(str(d.get("keyword"))): d for d in data}
                    for r in items:
                        d = values.get(normalize(r.keyword))
                        if d:
                            r.keyword_difficulty = d.get("keyword_difficulty")
            except Exception as exc:  # paid call: reported, never retried
                notes.append(
                    f"DataForSEO {step} for {market} failed: {type(exc).__name__}"
                )
    return round(provider.cost_usd - start_cost, 4), notes


def keyword_findings(rows: list[KeywordRow], base_url: str) -> list[Finding]:
    """Measured demand the site does not capture yet (needs provider volume)."""
    out = []
    for r in rows:
        if r.search_volume is None or r.search_volume < MIN_GAP_VOLUME:
            continue
        if r.gsc_position is not None and r.gsc_position <= 20:
            continue
        kd = r.keyword_difficulty
        out.append(
            finding(
                key=f"keyword-gap:{slug(r.keyword)}:{r.market.lower()}",
                issue=f"'{r.keyword}' has {r.search_volume} monthly searches in {r.market}; the site is not in the top 20",
                category="keywords/gap",
                priority="high"
                if r.search_volume >= 1000 and (kd is None or kd < 40)
                else "medium",
                evidence_items=[
                    evidence(
                        "DataForSEO (Google Ads volume, Labs difficulty) + Google Search Console",
                        f"volume {r.search_volume}, difficulty {kd if kd is not None else 'n/a'}, "
                        f"CPC {r.cpc if r.cpc is not None else 'n/a'}, site impressions {r.gsc_impressions:.0f}, "
                        f"site position {r.gsc_position if r.gsc_position is not None else 'none'}",
                    )
                ],
                seo_impact="Demand the site does not reach in this market.",
                action="Map the keyword to one owner page (extend it) or plan a new page; "
                "check intent on the live results first (seo-keyword-research).",
                outcome="The site appears for the keyword in this market.",
                verification="Search Console shows impressions for the keyword in this market.",
                skill=SKILL,
            )
        )
    return out


def backlink_findings(
    summaries: dict[str, dict[str, Any]], site_domain: str
) -> list[Finding]:
    """Referring-domain gap against direct competitors (counts as reported)."""
    ours = summaries.get(site_domain) or {}
    our_rd = ours.get("referring_domains")
    if our_rd is None:
        return []
    stronger = {
        d: s.get("referring_domains")
        for d, s in summaries.items()
        if d != site_domain
        and isinstance(s.get("referring_domains"), int)
        and s["referring_domains"] > our_rd * 2
    }
    if not stronger:
        return []
    return [
        finding(
            key="referring-domain-gap:site",
            issue=f"Competitors have over twice the referring domains ({our_rd} for the site)",
            category="offpage/backlinks",
            priority="medium",
            evidence_items=[
                evidence(
                    "DataForSEO Backlinks summary",
                    ", ".join(
                        f"{d}: {n}"
                        for d, n in sorted(stronger.items(), key=lambda kv: -kv[1])
                    ),
                )
            ],
            seo_impact="Lower authority makes competitive keywords harder to win.",
            action="Earn links with citable assets (original data, pass-rate guides, free tools), "
            "partnerships and digital PR; use seo-backlink-gap for domain-level prospects.",
            outcome="Referring domains grow month over month.",
            verification="Compare referring domains in the next keyword-data run.",
            skill=SKILL,
        )
    ]


def snapshot(rows: list[KeywordRow]) -> list[dict[str, Any]]:
    return [asdict(r) for r in rows]
