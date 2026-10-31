"""Extract technical SEO signals from raw HTML (never from Markdown).

Parsing uses BeautifulSoup with the stdlib ``html.parser``: nothing is executed,
nothing is fetched, and every list is capped. Scraped HTML is untrusted data; it
is only read, never followed as instructions. URLs are resolved against the page
URL (or ``<base href>``) and classified as internal when they are on the same
site (the host with or without a leading ``www.``).
"""

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field

MAX_LINKS = 1000
MAX_HEADINGS = 200
MAX_SAMPLES = 20
MAX_TEXT = 300
_SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "sms:", "ftp:", "about:")
_WS = re.compile(r"\s+")
_NAV_PARENTS = ("nav", "header", "footer", "aside")
_SCOPED_DIRECTIVES = frozenset(
    {"noindex", "nofollow", "none", "all", "index", "follow", "noarchive", "nosnippet"}
)


def _clean(text: str | None, limit: int = MAX_TEXT) -> str:
    return _WS.sub(" ", text or "").strip()[:limit]


def site_host(host: str) -> str:
    """Host without a leading ``www.`` (the unit of "same site" here)."""
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def is_internal(url: str, site: str) -> bool:
    host = urlsplit(url).hostname or ""
    return site_host(host) == site_host(site)


class Heading(BaseModel):
    level: int
    text: str


class Link(BaseModel):
    href: str
    text: str = ""
    rel: list[str] = []
    internal: bool
    nofollow: bool = False
    placement: str = "content"  # content | navigation


class Hreflang(BaseModel):
    lang: str
    href: str


class ImageStats(BaseModel):
    total: int = 0
    missing_alt: int = 0  # no alt attribute at all
    empty_alt: int = 0  # alt="" (valid for decorative images)
    missing_alt_samples: list[str] = []


class PageSignals(BaseModel):
    """Everything the audit rules read from one page's HTML."""

    titles: list[str] = []
    meta_descriptions: list[str] = []
    robots_meta: dict[str, list[str]] = Field(
        default_factory=dict, description="agent (robots/googlebot/...) -> directives"
    )
    canonicals: list[str] = []
    canonical_invalid: list[str] = []
    hreflang: list[Hreflang] = []
    html_lang: str | None = None
    viewport: str | None = None
    charset: str | None = None
    meta_refresh: str | None = None
    base_href: str | None = None
    headings: list[Heading] = []
    h1_count: int = 0
    images: ImageStats = ImageStats()
    links: list[Link] = []
    links_truncated: bool = False
    open_graph: dict[str, str] = {}
    mixed_content: list[str] = []
    json_ld_blocks: list[str] = []
    microdata_present: bool = False
    rdfa_present: bool = False
    word_count: int = 0


def _resolve(base: str, href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    if not href or href.startswith("#") or href.lower().startswith(_SKIP_SCHEMES):
        return None
    absolute = urljoin(base, href)
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return parts._replace(fragment="").geturl()


def _placement(tag: Tag) -> str:
    for parent in tag.parents:
        if parent.name in _NAV_PARENTS or parent.get("role") == "navigation":
            return "navigation"
    return "content"


def _attr(tag: Tag, name: str) -> str | None:
    """An attribute as text (multi-valued attributes are joined)."""
    value = tag.get(name)
    if value is None:
        return None
    return value if isinstance(value, str) else " ".join(value)


def parse_html(html: str, page_url: str, site: str) -> PageSignals:
    """Parse ``html`` fetched from ``page_url``; ``site`` is the audited host."""
    soup = BeautifulSoup(html, "html.parser")
    signals = PageSignals()

    base_tag = soup.find("base", href=True)
    base = page_url
    if isinstance(base_tag, Tag):
        resolved = _resolve(page_url, str(base_tag["href"]))
        if resolved:
            signals.base_href = resolved
            base = resolved

    signals.titles = [_clean(t.get_text(), 1000) for t in soup.find_all("title")]
    for tag in soup.find_all("meta"):
        name = str(tag.get("name") or "").strip().lower()
        content = tag.get("content")
        if not isinstance(content, str):
            if tag.get("charset"):
                signals.charset = _clean(str(tag["charset"]), 40)
            continue
        if name == "description":
            signals.meta_descriptions.append(_clean(content, 2000))
        elif name in ("robots", "googlebot", "googlebot-news", "bingbot"):
            directives = [d.strip().lower() for d in content.split(",") if d.strip()]
            signals.robots_meta.setdefault(name, []).extend(directives)
        elif name == "viewport":
            signals.viewport = _clean(content, 300)
        prop = str(tag.get("property") or "").strip().lower()
        if prop.startswith("og:") and prop not in signals.open_graph:
            signals.open_graph[prop] = _clean(content, 1000)
        if str(tag.get("http-equiv") or "").lower() == "refresh":
            signals.meta_refresh = _clean(content, 500)
        if tag.get("charset"):
            signals.charset = _clean(str(tag["charset"]), 40)

    for tag in soup.find_all("link", href=True):
        rels = [r.lower() for r in (tag.get("rel") or [])]
        href = str(tag["href"])
        if "canonical" in rels:
            resolved = _resolve(base, href)
            if resolved:
                signals.canonicals.append(resolved)
            else:
                signals.canonical_invalid.append(_clean(href, 300))
        if "alternate" in rels and tag.get("hreflang"):
            resolved = _resolve(base, href)
            if resolved:
                signals.hreflang.append(
                    Hreflang(lang=_clean(str(tag["hreflang"]), 20), href=resolved)
                )

    html_tag = soup.find("html")
    if isinstance(html_tag, Tag) and html_tag.get("lang"):
        signals.html_lang = _clean(str(html_tag["lang"]), 35)

    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        if len(signals.headings) < MAX_HEADINGS:
            signals.headings.append(
                Heading(level=int(tag.name[1]), text=_clean(tag.get_text()))
            )
    signals.h1_count = len(soup.find_all("h1"))

    stats = ImageStats()
    for img in soup.find_all("img"):
        stats.total += 1
        alt = img.get("alt")
        if alt is None:
            stats.missing_alt += 1
            raw_src = _attr(img, "src")
            src = _resolve(base, raw_src) or _clean(raw_src or "", 200)
            if len(stats.missing_alt_samples) < MAX_SAMPLES and src:
                stats.missing_alt_samples.append(src)
        elif not str(alt).strip():
            stats.empty_alt += 1
    signals.images = stats

    for anchor in soup.find_all("a", href=True):
        resolved = _resolve(base, str(anchor["href"]))
        if not resolved:
            continue
        if len(signals.links) >= MAX_LINKS:
            signals.links_truncated = True
            break
        rels = [r.lower() for r in (anchor.get("rel") or [])]
        signals.links.append(
            Link(
                href=resolved,
                text=_clean(anchor.get_text(), 200),
                rel=rels,
                internal=is_internal(resolved, site),
                nofollow="nofollow" in rels,
                placement=_placement(anchor),
            )
        )

    if page_url.lower().startswith("https://"):
        for tag_name, attr in (
            ("img", "src"),
            ("script", "src"),
            ("iframe", "src"),
            ("link", "href"),
            ("source", "src"),
            ("video", "src"),
            ("audio", "src"),
        ):
            for element in soup.find_all(tag_name, attrs={attr: True}):
                if tag_name == "link" and "stylesheet" not in [
                    r.lower() for r in (element.get("rel") or [])
                ]:
                    continue
                value = str(element[attr]).strip()
                if (
                    value.lower().startswith("http://")
                    and len(signals.mixed_content) < MAX_SAMPLES
                ):
                    signals.mixed_content.append(value[:300])

    for script in soup.find_all("script", attrs={"type": True}):
        if str(script["type"]).strip().lower() == "application/ld+json":
            signals.json_ld_blocks.append(script.string or script.get_text() or "")
    signals.microdata_present = soup.find(itemscope=True) is not None
    signals.rdfa_present = soup.find(typeof=True) is not None

    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    body = soup.find("body")
    text = (body or soup).get_text(" ")
    signals.word_count = len(text.split())
    return signals


def robots_directives(
    signals: PageSignals, header_values: list[str]
) -> dict[str, list[str]]:
    """Directives that apply to Google: meta ``robots``/``googlebot`` plus
    ``X-Robots-Tag`` values that are unscoped or scoped to googlebot."""
    meta = signals.robots_meta.get("robots", []) + signals.robots_meta.get(
        "googlebot", []
    )
    header: list[str] = []
    for value in header_values:
        for part in value.split(","):
            token = part.strip().lower()
            if not token:
                continue
            if ":" in token:
                agent, _, rest = (s.strip() for s in token.partition(":"))
                if agent in ("googlebot", "robots"):
                    token = rest  # "googlebot: noindex"
                elif rest.split(" ")[0] in _SCOPED_DIRECTIVES:
                    continue  # "otherbot: noindex" applies to another crawler
                # else a valued directive such as "max-snippet: 0"; keep whole
            header.append(token)
    return {"meta": meta, "header": header}


def has_noindex(directives: dict[str, list[str]]) -> bool:
    tokens = set(directives["meta"]) | set(directives["header"])
    return bool(tokens & {"noindex", "none"})
