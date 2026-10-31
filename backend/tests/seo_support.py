"""Offline fakes for technical SEO tests: a scripted website and a fake scraper.

``FakeSite`` answers direct HTTP (robots.txt, sitemaps, HEAD traces) through
``httpx.MockTransport``; ``FakeScraper`` stands in for Firecrawl's raw-HTML
scrape. Nothing touches the network; DNS is a fake public resolver.
"""

from dataclasses import dataclass, field

import httpx

from app.research.url_policy import UrlPolicy
from app.schemas.research import utc_now
from app.seo.technical.collector import RequestBudget, SiteCollector
from app.seo.technical.models import AuditLimits
from app.tools.errors import ProviderUnavailableError
from app.tools.firecrawl_tool import SeoScrape

PUBLIC_IP = "93.184.216.34"
SITE = "https://example.com"


async def public_resolver(host: str) -> list[str]:
    if host.endswith(".internal") or host.startswith("private."):
        return ["10.0.0.5"]  # a public-looking name that resolves to a private address
    return [PUBLIC_IP]


def policy(max_redirects: int = 5) -> UrlPolicy:
    return UrlPolicy(resolver=public_resolver, max_redirects=max_redirects)


@dataclass
class Resource:
    status: int = 200
    headers: dict[str, str] = field(default_factory=lambda: {"content-type": "text/html; charset=utf-8"})
    body: bytes = b""


@dataclass
class FakeSite:
    """URL -> response. Unknown URLs answer 404. Records every request."""

    resources: dict[str, Resource] = field(default_factory=dict)
    requests: list[tuple[str, str]] = field(default_factory=list)
    head_not_allowed: set[str] = field(default_factory=set)

    def add(self, url: str, status: int = 200, body: str | bytes = b"", **headers: str) -> None:
        hdrs = {"content-type": "text/html; charset=utf-8"}
        hdrs.update({k.replace("_", "-"): v for k, v in headers.items()})
        self.resources[url] = Resource(status, hdrs, body.encode() if isinstance(body, str) else body)

    def redirect(self, url: str, to: str, status: int = 301) -> None:
        self.resources[url] = Resource(status, {"location": to}, b"")

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((request.method, url))
        if request.method == "HEAD" and url in self.head_not_allowed:
            return httpx.Response(405)
        resource = self.resources.get(url)
        if resource is None:
            return httpx.Response(404, headers={"content-type": "text/html"})
        body = b"" if request.method == "HEAD" else resource.body
        return httpx.Response(resource.status, headers=resource.headers, content=body)

    def client(self, budget: RequestBudget | None = None) -> httpx.AsyncClient:
        hooks = {"request": [budget.on_request]} if budget else {}
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler), event_hooks=hooks)


@dataclass
class FakeScraper:
    """Raw HTML per URL; records calls (each call = one Firecrawl credit)."""

    pages: dict[str, str] = field(default_factory=dict)
    failing: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    configured: bool = True

    def ensure_configured(self) -> None:
        if not self.configured:
            from app.core.exceptions import ConfigurationException

            raise ConfigurationException("Firecrawl is not configured; set FIRECRAWL_API_KEY.")

    async def scrape_for_seo(self, url: str, *, max_html_chars: int) -> SeoScrape:
        self.calls.append(url)
        if url in self.failing:
            raise ProviderUnavailableError("Firecrawl", "unavailable")
        html = self.pages.get(url, "")
        return SeoScrape(url=url, provider_final_url=url, status_code=200, raw_html=html[:max_html_chars],
                         truncated=len(html) > max_html_chars, original_length=len(html), fetched_at=utc_now())


def page_html(title: str = "Page", *, description: str | None = "A description", canonical: str | None = None,
              body: str = "", head: str = "", lang: str | None = "en", viewport: bool = True) -> str:
    parts = [f"<!doctype html><html{f' lang={lang!r}' if lang else ''}><head>"]
    if title is not None:
        parts.append(f"<title>{title}</title>")
    if description is not None:
        parts.append(f'<meta name="description" content="{description}">')
    if canonical is not None:
        parts.append(f'<link rel="canonical" href="{canonical}">')
    if viewport:
        parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append(head)
    parts.append(f"</head><body><h1>{title}</h1>{body}</body></html>")
    return "".join(parts)


def collector(site: FakeSite, scraper: FakeScraper, *, max_pages: int = 10, max_link_checks: int = 20,
              max_sitemaps: int = 3, max_direct: int = 1000, max_scrapes: int | None = None,
              deadline: float = 1e12) -> tuple[SiteCollector, RequestBudget, httpx.AsyncClient]:
    budget = RequestBudget(max_direct_requests=max_direct, max_scrapes=max_scrapes or max_pages,
                           deadline=deadline, clock=lambda: 0.0)
    client = site.client(budget)
    limits = AuditLimits(max_pages=max_pages, max_link_checks=max_link_checks, max_sitemaps=max_sitemaps,
                         max_duration_seconds=600)
    return SiteCollector(policy=policy(), scraper=scraper, client=client, budget=budget, limits=limits,
                         max_html_chars=1_000_000, max_sitemap_bytes=1_000_000, max_sitemap_urls=5000,
                         concurrency=2), budget, client
