"""Website mapping: list a site's URLs, classify, score and filter them.

Uses Firecrawl's map endpoint (URL discovery only, no page content). Every
returned URL is untrusted: it is normalized, confined to the website's scope,
checked by ``UrlPolicy`` and deduplicated before it can be crawled. Page
classification is deterministic (URL path, then title keywords); a classifier
protocol allows a future LLM classifier without any LLM calls today.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit

from app.core.config import Settings, get_settings
from app.core.logging import log_event
from app.core.metrics import timed
from app.research.domains import same_site
from app.research.source_manager import SourceManager
from app.research.url_policy import UrlRejected
from app.schemas.intelligence import MappedPage, PageType
from app.tools.firecrawl_tool import MapLink

logger = logging.getLogger(__name__)

_TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "mc_", "ref", "ref_src")
_ASSET_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".css", ".js",
    ".xml", ".json", ".zip", ".gz", ".mp3", ".mp4", ".mov", ".avi", ".woff", ".woff2",
    ".ttf", ".docx", ".xlsx", ".pptx",
)  # fmt: skip
_EXCLUDED_SEGMENTS = {
    "login": "authentication_page",
    "signin": "authentication_page",
    "sign-in": "authentication_page",
    "signup": "authentication_page",
    "sign-up": "authentication_page",
    "register": "authentication_page",
    "logout": "authentication_page",
    "account": "authentication_page",
    "my-account": "authentication_page",
    "cart": "transactional_page",
    "checkout": "transactional_page",
    "wp-admin": "admin_page",
    "wp-login.php": "admin_page",
    "admin": "admin_page",
    "cdn-cgi": "admin_page",
    "feed": "feed",
    "tag": "taxonomy_archive",
    "tags": "taxonomy_archive",
    "category": "taxonomy_archive",
    "categories": "taxonomy_archive",
    "author": "taxonomy_archive",
}

# (page type, path/title keywords) in priority order.
_RULES: list[tuple[PageType, tuple[str, ...]]] = [
    (PageType.API_DOCUMENTATION, ("api-reference", "api-docs", "reference", "swagger", "openapi")),
    (PageType.PRICING, ("pricing", "plans", "price", "prices", "subscription", "packages")),
    (PageType.DOCUMENTATION, ("docs", "documentation", "guide", "guides", "help", "manual", "knowledge-base", "kb", "developers", "developer", "tutorials")),
    (PageType.FEATURES, ("features", "capabilities", "feature")),
    (PageType.INTEGRATIONS, ("integrations", "integration", "connectors", "apps", "marketplace")),
    (PageType.CASE_STUDY, ("case-study", "case-studies", "casestudy", "success-stories")),
    (PageType.CUSTOMER_STORY, ("customers", "customer-stories", "testimonials", "stories", "customer")),
    (PageType.SERVICE, ("services", "service", "consulting", "what-we-do", "offerings", "professional-services")),
    (PageType.PRODUCT, ("product", "products", "platform", "solutions", "solution", "software", "tools")),
    (PageType.SECURITY, ("security", "trust", "compliance", "gdpr", "soc2")),
    (PageType.CAREERS, ("careers", "jobs", "join-us", "hiring", "work-with-us")),
    (PageType.CONTACT, ("contact", "contact-us", "get-in-touch", "book-a-demo", "demo")),
    (PageType.ABOUT, ("about", "about-us", "who-we-are", "our-story", "mission")),
    (PageType.COMPANY, ("company", "team", "leadership", "press", "newsroom", "investors")),
    (PageType.BLOG, ("blog", "news", "articles", "insights", "posts", "post", "resources")),
]  # fmt: skip

_TYPE_WEIGHT = {
    PageType.PRICING: 0.9,
    PageType.PRODUCT: 0.9,
    PageType.SERVICE: 0.85,
    PageType.FEATURES: 0.85,
    PageType.HOMEPAGE: 0.8,
    PageType.DOCUMENTATION: 0.7,
    PageType.API_DOCUMENTATION: 0.65,
    PageType.INTEGRATIONS: 0.6,
    PageType.ABOUT: 0.6,
    PageType.COMPANY: 0.5,
    PageType.CASE_STUDY: 0.5,
    PageType.CUSTOMER_STORY: 0.5,
    PageType.SECURITY: 0.4,
    PageType.BLOG: 0.3,
    PageType.CONTACT: 0.2,
    PageType.CAREERS: 0.1,
    PageType.UNKNOWN: 0.25,
}


class PageClassifier(Protocol):
    """Classifier contract; implement with an LLM later without changing callers."""

    def classify(self, url: str, title: str | None = None) -> PageType: ...


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())) | set(
        re.findall(r"[a-z0-9]+", text.lower())
    )


class RuleBasedPageClassifier:
    """Deterministic classifier: URL path first, then title keywords."""

    def classify(self, url: str, title: str | None = None) -> PageType:
        parts = urlsplit(url)
        path = parts.path.strip("/")
        host_labels = (parts.hostname or "").split(".")
        if not path:
            if host_labels and host_labels[0] in ("docs", "developer", "developers"):
                return PageType.DOCUMENTATION
            if host_labels and host_labels[0] == "api":
                return PageType.API_DOCUMENTATION
            return PageType.HOMEPAGE
        found = self._match(_tokens(path))
        if found is not None:
            return found
        if host_labels and host_labels[0] in ("docs", "developer", "developers"):
            return PageType.DOCUMENTATION
        if title:
            found = self._match(_tokens(title))
            if found is not None:
                return found
        return PageType.UNKNOWN

    @staticmethod
    def _match(tokens: set[str]) -> PageType | None:
        for page_type, keywords in _RULES:
            if any(k in tokens for k in keywords):
                return page_type
        return None


def strip_tracking(url: str) -> str:
    """Drop tracking query parameters (they never change page content)."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PARAMS)
    ]
    query = urlencode(kept)
    return parts._replace(query=query).geturl()


def exclusion_reason(url: str) -> str | None:
    """A reason this URL should not be crawled, or None."""
    parts = urlsplit(url)
    path = parts.path.lower()
    if path.endswith(_ASSET_EXTENSIONS):
        return "non_html_asset"
    segments = [s for s in path.split("/") if s]
    for segment in segments:
        if segment in _EXCLUDED_SEGMENTS:
            return _EXCLUDED_SEGMENTS[segment]
    if re.search(r"/page/\d+/?$", path) or "page=" in parts.query.lower():
        return "pagination"
    return None


def score_page(
    page_type: PageType, url: str, title: str | None, query_terms: set[str], depth: int
) -> float:
    """Relevance 0..1 from page type, query overlap and path depth."""
    score = _TYPE_WEIGHT[page_type]
    haystack = _tokens(f"{urlsplit(url).path} {title or ''}")
    if query_terms:
        overlap = len(query_terms & haystack) / len(query_terms)
        score += 0.15 * overlap
    score -= 0.05 * max(depth - 1, 0)
    return round(max(0.0, min(score, 1.0)), 3)


class SiteMapper(Protocol):
    """Map adapter contract (``FirecrawlTool``)."""

    async def map_site(
        self,
        url: str,
        *,
        search: str | None = None,
        limit: int = 200,
        include_subdomains: bool = False,
    ) -> list[MapLink]: ...


@dataclass
class MapResult:
    """Outcome of mapping one website."""

    pages: list[MappedPage] = field(default_factory=list)
    discovered_urls: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def eligible(self) -> list[MappedPage]:
        """Pages that may be crawled, best first."""
        return sorted(
            (p for p in self.pages if p.crawl_eligible),
            key=lambda p: -p.relevance_score,
        )


class WebsiteMapper:
    """Discover, validate, classify and rank the URLs of one website."""

    def __init__(
        self,
        mapper: SiteMapper,
        sources: SourceManager,
        classifier: PageClassifier | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._mapper = mapper
        self._sources = sources
        self._classifier = classifier or RuleBasedPageClassifier()
        self._settings = settings or get_settings()

    async def map(
        self,
        website_url: str,
        query: str,
        *,
        max_urls: int | None = None,
        max_depth: int | None = None,
        search: str | None = None,
        allowed_domains: list[str] | None = None,
    ) -> MapResult:
        """Map ``website_url``; unsafe/out-of-scope/irrelevant URLs are kept as excluded."""
        cfg = self._settings
        limit = min(
            max_urls or cfg.website_research_max_map_urls,
            cfg.website_research_max_map_urls,
        )
        depth_limit = (
            cfg.website_research_max_crawl_depth if max_depth is None else max_depth
        )
        root = await self._sources.policy.validate(website_url, allowed_domains)
        with timed("map"):
            links = await self._mapper.map_site(
                root.normalized, search=search, limit=limit
            )
        result = MapResult(discovered_urls=len(links))
        terms = _tokens(query) - {"the", "and", "for", "of", "in", "a", "top"}
        seen: set[str] = set()
        host_ok: dict[str, str | None] = {root.host: None}

        candidates = [MapLink(url=root.normalized, title=None)] + links
        for link in candidates:
            page = await self._page_for(
                link, root.host, terms, depth_limit, allowed_domains, host_ok, seen
            )
            if page is not None:
                result.pages.append(page)
        if not result.pages[0:1]:
            result.notes.append("no pages were mapped")
        eligible = sum(p.crawl_eligible for p in result.pages)
        log_event(
            logger,
            "website_mapped",
            domain=root.host,
            discovered=len(links),
            eligible=eligible,
            excluded=len(result.pages) - eligible,
        )
        return result

    async def _page_for(
        self,
        link: MapLink,
        site_host: str,
        terms: set[str],
        depth_limit: int,
        allowed_domains: list[str] | None,
        host_ok: dict[str, str | None],
        seen: set[str],
    ) -> MappedPage | None:
        method = "homepage" if not seen else "firecrawl_map"
        try:
            normalized, host = self._sources.policy.normalize(
                strip_tracking(link.url), allowed_domains
            )
        except UrlRejected as exc:
            return self._excluded(link, method, f"url_rejected:{exc.reason}")
        if normalized in seen:
            return None
        seen.add(normalized)
        if not same_site(host, site_host):
            return self._excluded(
                link, method, "outside_website_scope", normalized, host
            )
        if host not in host_ok:
            host_ok[host] = await self._host_problem(normalized)
        if host_ok[host] is not None:
            return self._excluded(
                link, method, f"url_rejected:{host_ok[host]}", normalized, host
            )
        depth = len([s for s in urlsplit(normalized).path.split("/") if s])
        reason = exclusion_reason(normalized)
        if reason is None and depth > depth_limit:
            reason = "exceeds_max_depth"
        page_type = self._classifier.classify(normalized, link.title)
        return MappedPage(
            url=link.url,
            normalized_url=normalized,
            domain=host,
            title=link.title,
            page_type=page_type,
            relevance_score=score_page(page_type, normalized, link.title, terms, depth),
            discovery_method=method,
            crawl_eligible=reason is None,
            exclusion_reason=reason,
            depth=depth,
        )

    async def _host_problem(self, normalized_url: str) -> str | None:
        """Full (DNS-aware) validation once per distinct host; reason or None."""
        try:
            await self._sources.policy.validate(normalized_url)
        except UrlRejected as exc:
            return exc.reason
        return None

    @staticmethod
    def _excluded(
        link: MapLink,
        method: str,
        reason: str,
        normalized: str | None = None,
        host: str | None = None,
    ) -> MappedPage:
        return MappedPage(
            url=link.url,
            normalized_url=normalized or link.url,
            domain=host or (urlsplit(link.url).hostname or ""),
            title=link.title,
            discovery_method=method,
            crawl_eligible=False,
            exclusion_reason=reason,
        )
