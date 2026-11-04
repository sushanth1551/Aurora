"""Competitor change watch from public sitemaps (no page scraping by default).

Each run reads every configured direct/content competitor's robots.txt and
XML sitemaps through ``UrlPolicy`` (redirects re-validated, sizes capped), keeps
a snapshot of URL -> lastmod, and compares it with the previous snapshot:
new URLs, removed URLs and URLs whose lastmod changed. New URLs are matched to
the site's seed keywords and its own pages, so "they published on a topic we
cover / do not cover" is visible. Titles of a few new URLs can be fetched
(bounded by ``max_title_fetches``).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from app.research.robots import USER_AGENT
from app.research.url_policy import UrlPolicy
from app.seo.intelligence.insights.common import evidence, finding, page_path
from app.seo.intelligence.models import Competitor, Finding, WebsiteConfig
from app.seo.technical.site_files import fetch_site_file, parse_robots, parse_sitemap

SKILL = "seo-competitor-watch"
MAX_FILE_BYTES = 10_000_000
MAX_SITEMAPS_PER_SITE = 25
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


@dataclass
class CompetitorSnapshot:
    domain: str
    urls: dict[str, str]  # url -> lastmod ('' when absent)
    sitemaps_read: int
    notes: list[str] = field(default_factory=list)


@dataclass
class CompetitorDiff:
    domain: str
    new: list[str]
    removed: list[str]
    updated: list[str]
    topic_matches: dict[str, list[str]] = field(default_factory=dict)  # url -> keywords
    uncovered: list[str] = field(
        default_factory=list
    )  # new URLs on topics the site lacks
    titles: dict[str, str] = field(default_factory=dict)
    bulk_lastmod: bool = False  # many updates share almost the same timestamp


def _domains(domain: str) -> list[str]:
    bare = domain.lower().removeprefix("www.")
    return [bare, "www." + bare]


async def read_sitemaps(
    competitor: Competitor, policy: UrlPolicy, client: httpx.AsyncClient, max_urls: int
) -> CompetitorSnapshot:
    allowed = _domains(competitor.domain)
    base = f"https://{competitor.domain.removeprefix('www.')}"
    robots = await fetch_site_file(
        base + "/robots.txt",
        policy=policy,
        client=client,
        allowed_domains=allowed,
        max_bytes=500_000,
    )
    queue = parse_robots(robots.text).sitemaps if robots.text else []
    queue = [u for u in queue if any(d in u for d in allowed)] or [
        base + "/sitemap.xml"
    ]
    urls: dict[str, str] = {}
    seen: set[str] = set()
    snap = CompetitorSnapshot(domain=competitor.domain, urls=urls, sitemaps_read=0)
    while queue and snap.sitemaps_read < MAX_SITEMAPS_PER_SITE and len(urls) < max_urls:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        file = await fetch_site_file(
            current,
            policy=policy,
            client=client,
            allowed_domains=allowed,
            max_bytes=MAX_FILE_BYTES,
        )
        report = parse_sitemap(file, max_urls - len(urls))
        snap.sitemaps_read += 1
        if report.kind == "sitemapindex":
            queue += [c for c in report.children if c not in seen]
        elif report.kind == "urlset":
            for u in report.urls:
                urls[u] = report.lastmods.get(u, "")
        else:
            snap.notes.append(f"{current}: {report.kind} ({report.error})")
    if queue and len(urls) < max_urls:
        snap.notes.append(
            f"{len(queue)} sitemap(s) not read (limit {MAX_SITEMAPS_PER_SITE})."
        )
    return snap


def diff(
    previous: dict[str, str] | None, current: CompetitorSnapshot
) -> CompetitorDiff:
    if previous is None:  # first run: baseline only
        return CompetitorDiff(domain=current.domain, new=[], removed=[], updated=[])
    now = current.urls
    updated = sorted(
        u for u in now if u in previous and now[u] and now[u] != previous[u]
    )
    stamps = {now[u][:16] for u in updated}  # to the minute
    return CompetitorDiff(
        domain=current.domain,
        new=sorted(u for u in now if u not in previous),
        removed=sorted(u for u in previous if u not in now),
        updated=updated,
        bulk_lastmod=len(updated) >= 20 and len(stamps) <= 3,
    )


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def classify(d: CompetitorDiff, config: WebsiteConfig, own_paths: list[str]) -> None:
    """Mark new competitor URLs that match the site's seed keywords, and those
    whose topic no page of the site covers (slug-word overlap below a third)."""
    keywords = config.rank_tracking.keywords + config.seed_keywords
    own = [_words(p.replace("-", " ")) for p in own_paths]
    for url in d.new:
        words = _words(page_path(url).replace("-", " ").replace("/", " "))
        hits = [k for k in keywords if _words(k) and _words(k) <= words]
        if hits:
            d.topic_matches[url] = hits
            best = max((len(words & o) / len(words | o) for o in own if o), default=0.0)
            if best < 1 / 3:
                d.uncovered.append(url)


async def fetch_titles(
    urls: list[str], domain: str, policy: UrlPolicy, client: httpx.AsyncClient
) -> dict[str, str]:
    out = {}
    for url in urls:
        file = await fetch_site_file(
            url,
            policy=policy,
            client=client,
            allowed_domains=_domains(domain),
            max_bytes=1_500_000,
        )
        if file.text:
            m = TITLE_RE.search(file.text)
            if m:
                out[url] = " ".join(m.group(1).split())[:200]
    return out


async def watch(
    config: WebsiteConfig,
    previous: dict[str, dict[str, str]],
    own_paths: list[str],
    *,
    policy: UrlPolicy | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[CompetitorSnapshot], list[CompetitorDiff]]:
    policy = policy or UrlPolicy()
    settings = config.competitor_watch
    owned = client is None
    client = client or httpx.AsyncClient(
        timeout=20, follow_redirects=False, headers={"User-Agent": USER_AGENT}
    )
    snaps, diffs = [], []
    try:
        for comp in config.competitors:
            if comp.relevance not in ("direct", "content"):
                continue
            snap = await read_sitemaps(comp, policy, client, settings.max_urls_per_site)
            d = diff(previous.get(comp.domain), snap)
            classify(d, config, own_paths)
            interesting = (d.uncovered + list(d.topic_matches))[
                : settings.max_title_fetches
            ]
            if interesting:
                d.titles = await fetch_titles(
                    list(dict.fromkeys(interesting)), comp.domain, policy, client
                )
            snaps.append(snap)
            diffs.append(d)
    finally:
        if owned:
            await client.aclose()
    return snaps, diffs


def watch_findings(diffs: list[CompetitorDiff]) -> list[Finding]:
    out = []
    for d in diffs:
        if d.uncovered:
            sample = "; ".join(
                f"{u}" + (f" — {d.titles[u]}" if u in d.titles else "")
                for u in d.uncovered[:8]
            )
            out.append(
                finding(
                    key=f"competitor-new-topics:{d.domain}",
                    issue=f"{d.domain} published {len(d.uncovered)} page(s) on your keywords that you do not cover",
                    category="competitor/content",
                    priority="medium",
                    evidence_items=[
                        evidence(
                            f"{d.domain} XML sitemap (compared with the previous snapshot)",
                            sample,
                        )
                    ],
                    seo_impact="Competitors are building topical coverage for queries in your market.",
                    action="Review each page's intent; decide whether to extend an existing page or plan a "
                    "better one (seo-content-strategy). Do not copy.",
                    outcome="A decision is recorded for each topic.",
                    verification="Next /seo-competitor-watch run; the topics are covered or rejected.",
                    skill=SKILL,
                    urls=d.uncovered[:50],
                )
            )
        if len(d.updated) >= 5:
            out.append(
                finding(
                    key=f"competitor-refresh-wave:{d.domain}",
                    issue=f"{d.domain} updated {len(d.updated)} pages since the last run"
                    + (
                        " (one bulk timestamp: likely an automatic update, not content edits)"
                        if d.bulk_lastmod
                        else ""
                    ),
                    category="competitor/content",
                    priority="low",
                    evidence_items=[
                        evidence(
                            f"{d.domain} XML sitemap lastmod", "; ".join(d.updated[:8])
                        )
                    ],
                    seo_impact="Refreshed competitor pages can overtake older pages on the same queries.",
                    action="Check whether any of these pages compete with your top pages; refresh yours if so.",
                    outcome="Your competing pages are at least as current.",
                    verification="Next /seo-competitor-watch run.",
                    skill=SKILL,
                    urls=d.updated[:50],
                )
            )
    return out


def snapshot(
    snaps: list[CompetitorSnapshot], diffs: list[CompetitorDiff]
) -> dict[str, Any]:
    return {
        "competitors": [
            {
                "domain": s.domain,
                "urls": len(s.urls),
                "sitemaps_read": s.sitemaps_read,
                "notes": s.notes,
            }
            for s in snaps
        ],
        "changes": [
            {k: v for k, v in asdict(d).items()}
            | {
                "new_count": len(d.new),
                "removed_count": len(d.removed),
                "updated_count": len(d.updated),
            }
            for d in diffs
        ],
    }
