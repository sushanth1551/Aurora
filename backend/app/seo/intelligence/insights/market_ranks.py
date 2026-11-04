"""Rank tracking per market: free Search Console positions + budgeted SERP checks.

- **Search Console** (``query x country``) gives the site's average position per
  tracked keyword and market on every run, at no cost. It is an impression-
  weighted average, not a single live rank.
- **SerpApi spot checks** (optional) observe one live result page per keyword,
  market and device: the site's position, the top competitors, who owns the
  featured snippet, and whether an AI Overview appears and cites the site.
  They run only within the owner's monthly search budget and never retry.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Protocol

from app.seo.intelligence.insights.common import (
    GscRow,
    evidence,
    finding,
    slug,
)
from app.seo.intelligence.models import Finding, RankTracking

SKILL = "seo-rank-tracking"
MOVE_THRESHOLD = 3.0

ISO2_TO_GSC = {
    "IN": "ind",
    "US": "usa",
    "GB": "gbr",
    "CA": "can",
    "AU": "aus",
    "AE": "are",
    "SA": "sau",
    "PK": "pak",
    "BD": "bgd",
    "NP": "npl",
    "LK": "lka",
    "PH": "phl",
    "NG": "nga",
    "EG": "egy",
    "KE": "ken",
    "MY": "mys",
    "SG": "sgp",
    "NZ": "nzl",
    "IE": "irl",
    "DE": "deu",
    "FR": "fra",
    "QA": "qat",
    "KW": "kwt",
    "OM": "omn",
}
SERP_LOCATION = {
    "IN": "India",
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "AE": "United Arab Emirates",
}


def normalize(query: str) -> str:
    return " ".join(query.lower().split())


@dataclass
class MarketRank:
    keyword: str
    market: str
    period_end: str
    position: float | None
    impressions: float
    clicks: float
    previous_position: float | None = None
    source: str = "Google Search Console (query x country)"

    @property
    def movement(self) -> float | None:
        if self.position is None or self.previous_position is None:
            return None
        return round(self.previous_position - self.position, 1)  # positive = better


def gsc_market_ranks(
    rows: list[GscRow],
    keywords: list[str],
    markets: list[str],
    period_end: date,
    history: list[dict[str, Any]],
) -> list[MarketRank]:
    """``rows`` are ``query x country`` rows for the tracking window."""
    wanted = {normalize(k): k for k in keywords}
    acc: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    gsc_to_iso = {ISO2_TO_GSC.get(m, ""): m for m in markets}
    for row in rows:
        keyword = wanted.get(normalize(row.get("query")))
        market = gsc_to_iso.get(row.get("country").lower())
        if keyword is None or market is None:
            continue
        a = acc[(keyword, market)]
        a[0] += row.position * row.impressions
        a[1] += row.impressions
        a[2] += row.clicks
    last: dict[tuple[str, str], float] = {}
    for entry in sorted(history, key=lambda e: str(e.get("period_end"))):
        if (
            entry.get("position") is not None
            and str(entry.get("period_end")) < period_end.isoformat()
        ):
            last[(str(entry["keyword"]), str(entry["market"]))] = float(
                entry["position"]
            )
    out = []
    for keyword in keywords:
        for market in markets:
            weighted, impressions, clicks = acc.get((keyword, market), [0.0, 0.0, 0.0])
            out.append(
                MarketRank(
                    keyword=keyword,
                    market=market,
                    period_end=period_end.isoformat(),
                    position=round(weighted / impressions, 1) if impressions else None,
                    impressions=impressions,
                    clicks=clicks,
                    previous_position=last.get((keyword, market)),
                )
            )
    return out


# --- SERP spot checks -------------------------------------------------------------------


class SerpSearcher(Protocol):
    async def search_serp(
        self,
        query: str,
        *,
        num_results: int = 10,
        country: str | None = None,
        language: str | None = None,
        device: str | None = None,
        location: str | None = None,
    ) -> Any: ...


@dataclass
class SerpCheck:
    keyword: str
    market: str
    device: str
    checked_on: str
    our_position: int | None
    our_url: str | None
    top_domains: list[str]
    featured_snippet_domain: str | None
    ai_overview: str  # present | absent | requires_second_request | unknown
    ai_overview_cites_site: bool | None
    ai_overview_domains: list[str] = field(default_factory=list)
    people_also_ask: list[str] = field(default_factory=list)


def month_key(day: date) -> str:
    return day.strftime("%Y-%m")


def budget_left(usage: dict[str, int], settings: RankTracking, today: date) -> int:
    return max(0, settings.serpapi_monthly_budget - int(usage.get(month_key(today), 0)))


def _domain_matches(domain: str | None, site_domain: str) -> bool:
    if not domain:
        return False
    d = domain.lower().removeprefix("www.")
    return d == site_domain or d.endswith("." + site_domain)


def check_from_snapshot(
    snapshot: Any, keyword: str, market: str, device: str, site_domain: str, today: date
) -> SerpCheck:
    organic = list(getattr(snapshot, "organic", []) or [])
    ours = next((r for r in organic if _domain_matches(r.domain, site_domain)), None)
    features = snapshot.features
    snippet = features.featured_snippet
    ai = features.ai_overview
    cites = None
    if ai.status == "present":
        cites = any(_domain_matches(d, site_domain) for d in ai.cited_domains)
    return SerpCheck(
        keyword=keyword,
        market=market,
        device=device,
        checked_on=today.isoformat(),
        our_position=ours.position if ours else None,
        our_url=ours.url if ours else None,
        top_domains=[r.domain or "" for r in organic[:5]],
        featured_snippet_domain=getattr(snippet, "domain", None) if snippet else None,
        ai_overview=ai.status,
        ai_overview_cites_site=cites,
        ai_overview_domains=list(ai.cited_domains)[:10],
        people_also_ask=[q.question for q in features.people_also_ask][:6],
    )


async def serp_checks(
    searcher: SerpSearcher,
    keywords: list[str],
    settings: RankTracking,
    site_domain: str,
    language: str,
    today: date,
    max_searches: int,
) -> tuple[list[SerpCheck], int, list[str]]:
    """Run at most ``max_searches`` searches (one per keyword x market x device).
    Returns checks, searches spent and notes on anything skipped or failed."""
    checks: list[SerpCheck] = []
    notes: list[str] = []
    spent = 0
    for keyword in keywords:
        for market in settings.markets:
            for device in settings.devices:
                if spent >= max_searches:
                    notes.append(
                        f"Budget reached; '{keyword}' {market} {device} not checked."
                    )
                    continue
                spent += 1
                try:
                    snap = await searcher.search_serp(
                        keyword,
                        num_results=10,
                        country=market.lower(),
                        language=language,
                        device=device,
                        location=SERP_LOCATION.get(market),
                    )
                except Exception as exc:  # reported, never retried (paid call)
                    notes.append(f"'{keyword}' {market} {device}: {type(exc).__name__}")
                    continue
                checks.append(
                    check_from_snapshot(
                        snap, keyword, market, device, site_domain, today
                    )
                )
    return checks, spent, notes


# --- Findings -----------------------------------------------------------------------------


def rank_findings(
    ranks: list[MarketRank], checks: list[SerpCheck], base_url: str
) -> list[Finding]:
    out = []
    for r in ranks:
        move = r.movement
        if move is not None and move <= -MOVE_THRESHOLD:
            out.append(
                finding(
                    key=f"keyword-ranking-drop:{slug(r.keyword)}:{r.market.lower()}",
                    issue=f"'{r.keyword}' dropped in {r.market}: {r.previous_position} -> {r.position}",
                    category="keywords/ranking",
                    priority="high" if (r.previous_position or 99) <= 10 else "medium",
                    evidence_items=[
                        evidence(
                            r.source,
                            f"Average position {r.previous_position} on the previous run, "
                            f"{r.position} for the window ending {r.period_end} "
                            f"({r.impressions:.0f} impressions, {r.clicks:.0f} clicks).",
                        )
                    ],
                    seo_impact="Fewer clicks for a tracked keyword in this market.",
                    action="Look at the live result for this market (seo-rank-tracking SERP check), "
                    "the ranking page and recent changes before acting.",
                    outcome="Position recovers to its previous level.",
                    verification="Next rank-tracking run shows the previous position or better.",
                    skill=SKILL,
                )
            )
    for c in checks:
        if (
            c.featured_snippet_domain
            and c.our_position
            and c.our_position <= 5
            and not _domain_matches(
                c.featured_snippet_domain,
                base_url.split("//")[-1].split("/")[0].removeprefix("www."),
            )
        ):
            out.append(
                finding(
                    key=f"featured-snippet-lost:{slug(c.keyword)}:{c.market.lower()}",
                    issue=f"'{c.keyword}' ({c.market}): {c.featured_snippet_domain} owns the featured snippet; the site ranks {c.our_position}",
                    category="serp/features",
                    priority="medium",
                    evidence_items=[
                        evidence(
                            "SerpApi (Google, live result)",
                            f"{c.device}, checked {c.checked_on}",
                            c.our_url,
                        )
                    ],
                    seo_impact="The snippet takes clicks above the organic results.",
                    action="Answer the query in 40-60 words directly under a matching heading "
                    "(paragraph, list or table, matching the current snippet's format).",
                    outcome="The site wins or shares the featured snippet.",
                    verification="Next SERP check shows the site as the snippet source.",
                    skill=SKILL,
                    urls=[c.our_url] if c.our_url else [],
                )
            )
    return out


def snapshot(ranks: list[MarketRank], checks: list[SerpCheck]) -> dict[str, Any]:
    return {
        "market_ranks": [asdict(r) | {"movement": r.movement} for r in ranks],
        "serp_checks": [asdict(c) for c in checks],
    }


def history_entries(ranks: list[MarketRank]) -> list[dict[str, Any]]:
    return [
        {
            "period_end": r.period_end,
            "keyword": r.keyword,
            "market": r.market,
            "position": r.position,
            "impressions": r.impressions,
            "clicks": r.clicks,
        }
        for r in ranks
    ]
