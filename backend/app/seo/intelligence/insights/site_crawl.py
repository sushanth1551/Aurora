"""Free, repeatable full-site crawl for SEO Intelligence.

Reuses the technical audit's ``SiteCollector`` and every rule in
``seo.technical.rules`` (robots, sitemaps, status codes, redirects,
indexability, titles, descriptions, H1s, canonicals, hreflang, structured
data, images' alt text, mixed content, internal links), but fetches page HTML
directly through ``UrlPolicy`` instead of Firecrawl, so a crawl costs no credits
and the same input gives the same findings.

Extra checks the rules do not cover: soft 404s, images without width/height
(layout shift), missing security headers and thin blog posts. The fetched HTML
stays in memory for the YMYL, video and source-map checks of the same run.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.core.config import Settings
from app.research.robots import USER_AGENT
from app.research.url_policy import UrlPolicy
from app.seo.intelligence.insights.common import evidence, finding, page_path
from app.seo.intelligence.models import Finding, Priority, WebsiteConfig
from app.seo.technical.collector import (
    CollectionResult,
    RequestBudget,
    SiteCollector,
)
from app.seo.technical.models import AuditLimits
from app.seo.technical.models import Finding as TechFinding
from app.seo.technical.rules import run_rules
from app.tools.firecrawl_tool import SeoScrape

SKILL = "seo-site-crawl"
MAX_PAGE_BYTES = 2_000_000
SECURITY_HEADERS = (
    "strict-transport-security",
    "x-content-type-options",
    "referrer-policy",
)
SEVERITY_TO_PRIORITY: dict[str, Priority] = {
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "low",
}


@dataclass
class DirectHtmlScraper:
    """``PageScraper`` that GETs HTML itself (no provider, no credits)."""

    policy: UrlPolicy
    site_domain: str
    client: httpx.AsyncClient
    fetch_source: str = "direct_html"
    html: dict[str, str] = field(default_factory=dict)
    headers: dict[str, dict[str, str]] = field(default_factory=dict)

    async def scrape_for_seo(self, url: str, *, max_html_chars: int) -> SeoScrape:
        checked = await self.policy.validate(url, allowed_domains=[self.site_domain])
        body = b""
        async with self.client.stream("GET", checked.normalized) as response:
            status = response.status_code
            self.headers[url] = {k.lower(): v for k, v in response.headers.items()}
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > MAX_PAGE_BYTES:
                    break
        text = body.decode("utf-8", errors="replace")
        truncated = len(text) > max_html_chars
        self.html[url] = text
        return SeoScrape(
            url=url,
            provider_final_url=url,
            status_code=status,
            raw_html=text[:max_html_chars],
            truncated=truncated,
            original_length=len(text),
            fetched_at=datetime.now(UTC),
        )


@dataclass
class CrawlOutput:
    result: CollectionResult
    rule_findings: list[TechFinding]
    html: dict[str, str]
    headers: dict[str, dict[str, str]]
    soft_404_status: int | None = None


async def crawl(
    config: WebsiteConfig,
    settings: Settings,
    *,
    max_pages: int = 150,
    max_link_checks: int = 300,
    max_seconds: int = 600,
    probe_soft_404: bool = True,
) -> CrawlOutput:
    policy = UrlPolicy()
    limits = AuditLimits(
        max_pages=max_pages,
        max_link_checks=max_link_checks,
        max_sitemaps=settings.seo_audit_max_sitemaps,
        max_duration_seconds=max_seconds,
    )
    budget = RequestBudget(
        max_direct_requests=max_pages * 3 + max_link_checks + 50,
        max_scrapes=max_pages,
        deadline=time.monotonic() + max_seconds,
    )
    async with httpx.AsyncClient(
        timeout=settings.seo_audit_http_timeout_seconds,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
        event_hooks={"request": [budget.on_request]},
    ) as client:
        scraper = DirectHtmlScraper(policy, config.domain, client)
        collector = SiteCollector(
            policy=policy,
            scraper=scraper,
            client=client,
            budget=budget,
            limits=limits,
            max_html_chars=settings.seo_audit_max_html_chars,
            max_sitemap_bytes=settings.seo_audit_max_sitemap_bytes,
            max_sitemap_urls=settings.seo_audit_max_sitemap_urls,
            concurrency=settings.seo_audit_concurrency,
        )
        result = await collector.collect(config.base_url)
        soft = None
        if probe_soft_404:
            probe = (
                config.base_url.rstrip("/")
                + f"/aurora-404-probe-{secrets.token_hex(6)}"
            )
            try:
                checked = await policy.validate(probe, allowed_domains=[config.domain])
                response = await client.get(checked.normalized)
                soft = response.status_code
            except Exception:  # unreachable probe: reported as not measured
                soft = None
    return CrawlOutput(
        result=result,
        rule_findings=run_rules(result),
        html=scraper.html,
        headers=scraper.headers,
        soft_404_status=soft,
    )


# --- Conversion and extra checks -----------------------------------------------------------


def _observation(observed: dict[str, Any]) -> str:
    return json.dumps(observed, ensure_ascii=False, default=str)[:600]


def convert(tech: list[TechFinding], base_url: str) -> list[Finding]:
    """Technical-audit findings as knowledge-base findings (stable key per check)."""
    out = []
    for t in tech:
        items = [
            evidence(
                "AURORA crawl (direct HTML)"
                if e.source == "firecrawl_raw_html"
                else f"AURORA crawl ({e.source})",
                _observation(e.observed) or t.detail,
                e.url,
            )
            for e in t.evidence[:10]
        ] or [evidence("AURORA crawl", t.detail)]
        out.append(
            finding(
                key=f"tech:{t.check_id}",
                issue=t.title,
                category=f"technical/{t.category}",
                priority=SEVERITY_TO_PRIORITY.get(t.severity, "low"),
                evidence_items=items,
                seo_impact=t.detail,
                action=f"Fix on the {t.affected_count or len(t.affected_urls)} affected URL(s); "
                "see the technical rule's guidance (seo-toolkit-technical-audit).",
                outcome="The check passes on the next crawl.",
                verification="Re-run /seo-site-crawl; the finding is not observed.",
                skill=SKILL,
                urls=t.affected_urls[:50],
            )
        )
    return out


def image_dimension_gaps(html_by_url: dict[str, str]) -> dict[str, tuple[int, int]]:
    """URL -> (images without width/height, all images); lazy placeholders count."""
    out = {}
    for url, html in html_by_url.items():
        soup = BeautifulSoup(html, "html.parser")
        imgs = soup.find_all("img")
        missing = [
            i
            for i in imgs
            if not (i.get("width") and i.get("height"))
            and "aspect-ratio" not in str(i.get("style") or "")
        ]
        if missing:
            out[url] = (len(missing), len(imgs))
    return out


def extra_findings(output: CrawlOutput, config: WebsiteConfig) -> list[Finding]:
    """Checks the technical rules do not cover, keyed like the rest of the ledger
    (``<check>:<scope>``) so they update existing findings instead of duplicating."""
    base = config.base_url.rstrip("/")
    out: list[Finding] = []
    if output.soft_404_status is not None and 200 <= output.soft_404_status < 300:
        out.append(
            finding(
                key="soft-404:site",
                issue="Unknown URLs return HTTP 200 instead of 404 (soft 404)",
                category="technical/status-codes",
                priority="high",
                evidence_items=[
                    evidence(
                        "AURORA probe of a random non-existent URL",
                        f"status {output.soft_404_status}",
                    )
                ],
                seo_impact="Search engines can index junk URLs as duplicates, and broken links never surface.",
                action="Return a real 404 status (with a helpful page) for unmatched routes in the web server or app router.",
                outcome="Unknown URLs return 404.",
                verification="Re-run /seo-site-crawl; the probe returns 404.",
                skill=SKILL,
            )
        )
    for url, (missing, total) in sorted(image_dimension_gaps(output.html).items()):
        out.append(
            finding(
                key=f"image-dimensions-missing:{page_path(url)}",
                issue=f"{missing} of {total} images on {page_path(url)} have no width/height",
                category="performance/layout",
                priority="medium" if missing >= 5 else "low",
                evidence_items=[
                    evidence(
                        "AURORA crawl (direct HTML)",
                        f"{missing}/{total} <img> without width/height or aspect-ratio",
                        url,
                    )
                ],
                seo_impact="Images without reserved space cause layout shift (CLS, a Core Web Vital).",
                action="Add width and height (or CSS aspect-ratio) to every <img>.",
                outcome="No image without reserved dimensions.",
                verification="Re-run /seo-site-crawl; confirm CLS with PageSpeed.",
                skill=SKILL,
                urls=[url],
            )
        )
    home = next((h for u, h in output.headers.items() if page_path(u) == "/"), None)
    if home is not None:
        missing_headers = [h for h in SECURITY_HEADERS if h not in home]
        if "x-frame-options" not in home and "frame-ancestors" not in home.get(
            "content-security-policy", ""
        ):
            missing_headers.append("x-frame-options / CSP frame-ancestors")
        if missing_headers:
            out.append(
                finding(
                    key="security-headers-missing:site",
                    issue="Security headers missing on the homepage: "
                    + ", ".join(missing_headers),
                    category="technical/security",
                    priority="medium",
                    evidence_items=[
                        evidence(
                            "AURORA crawl (response headers)",
                            ", ".join(missing_headers),
                            base + "/",
                        )
                    ],
                    seo_impact="Low direct ranking impact; standard trust and safety hygiene.",
                    action="Add the headers in the web server (nginx) configuration.",
                    outcome="The headers are present.",
                    verification="Re-run /seo-site-crawl.",
                    skill=SKILL,
                )
            )
    minimum = config.rules.min_words_blog_post
    if minimum:
        for p in output.result.pages:
            url = p.final_url or p.url
            if p.signals and "/blog/" in url and p.signals.word_count < minimum:
                out.append(
                    finding(
                        key=f"thin-content:{page_path(url)}",
                        issue=f"{page_path(url)} has {p.signals.word_count} words (site minimum {minimum})",
                        category="content/thin",
                        priority="medium",
                        evidence_items=[
                            evidence(
                                "AURORA crawl (visible word count)",
                                f"{p.signals.word_count} words",
                                url,
                            )
                        ],
                        seo_impact="Thin posts rarely satisfy the query fully.",
                        action="Expand with sourced, specific detail or merge into a stronger post.",
                        outcome="The post meets the site's minimum.",
                        verification="Re-run /seo-site-crawl.",
                        skill=SKILL,
                        urls=[url],
                    )
                )
    return out


def checkable_keys(output: CrawlOutput, config: WebsiteConfig) -> list[str]:
    """Keys this crawl could observe, so absent ones count as rechecked."""
    keys = ["soft-404:site"] if output.soft_404_status is not None else []
    if any(page_path(u) == "/" for u in output.headers):
        keys.append("security-headers-missing:site")
    for url in output.html:
        keys.append(f"image-dimensions-missing:{page_path(url)}")
        if config.rules.min_words_blog_post and "/blog/" in url:
            keys.append(f"thin-content:{page_path(url)}")
    return keys


def snapshot(output: CrawlOutput) -> dict[str, Any]:
    r = output.result
    return {
        "start_url": r.start_url,
        "coverage": r.coverage.model_dump(),
        "usage": r.usage.model_dump(),
        "pages": [
            {
                "url": p.final_url or p.url,
                "status": p.status_code,
                "indexability": p.indexability.state,
                "title": (
                    p.signals.titles[0] if p.signals and p.signals.titles else None
                ),
                "words": p.signals.word_count if p.signals else None,
            }
            for p in r.pages
        ],
        "rule_checks": sorted({t.check_id for t in output.rule_findings}),
        "soft_404_status": output.soft_404_status,
    }


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()
