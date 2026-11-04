"""robots.txt and XML sitemap fetching and inspection.

Fetching: every URL (and every redirect hop, at most ``MAX_FILE_REDIRECTS``) is
validated by ``UrlPolicy`` and restricted to the audited site; redirects are
followed manually; bodies are size-capped while streaming; gzip is
decompressed with the same cap.

robots.txt is matched per RFC 9309 (the rules Google documents): the most
specific matching user-agent group, ``*`` and ``$`` wildcards, longest match
wins, ``Allow`` wins ties. ``urllib.robotparser`` is not used because it does
not implement wildcards or longest-match precedence. Status semantics follow
RFC 9309 as well: 4xx means "no restrictions"; 5xx or unreachable means a
crawler should assume full disallow.

Sitemaps are parsed with the stdlib XML parser after rejecting any DOCTYPE or
ENTITY declaration (no entity expansion is possible).
"""

import gzip
import re
import zlib
from collections.abc import Iterable
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel

from app.research.robots import USER_AGENT
from app.research.url_policy import UrlPolicy, UrlRejected

MAX_FILE_REDIRECTS = 4
MAX_ROBOTS_BYTES = 512 * 1024  # Google reads the first 500 KiB
MAX_SITEMAP_URLS_KEPT = 50_000
_DECL = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class FetchedFile(BaseModel):
    url: str
    final_url: str | None = None
    outcome: str  # ok | client_error | server_error | unreachable | blocked | too_large | too_many_redirects
    status: int | None = None
    content_type: str | None = None
    size: int = 0
    redirects: list[str] = []
    text: str | None = None
    detail: str | None = None


async def fetch_site_file(
    url: str,
    *,
    policy: UrlPolicy,
    client: httpx.AsyncClient,
    allowed_domains: Iterable[str],
    max_bytes: int,
) -> FetchedFile:
    """GET a small site file without automatic redirects.

    Never raises for network or policy problems (they become ``outcome``); a
    budget exception raised by the client's request hook propagates.
    """
    current = url
    redirects: list[str] = []
    for _ in range(MAX_FILE_REDIRECTS + 1):
        try:
            current = (await policy.validate(current, allowed_domains)).normalized
        except UrlRejected as exc:
            return FetchedFile(
                url=url, outcome="blocked", redirects=redirects, detail=exc.reason
            )
        try:
            async with client.stream(
                "GET",
                current,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT},
            ) as response:
                if response.status_code in (
                    301,
                    302,
                    303,
                    307,
                    308,
                ) and response.headers.get("location"):
                    redirects.append(current)
                    current = urljoin(current, response.headers["location"])
                    continue
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        return FetchedFile(
                            url=url,
                            final_url=current,
                            outcome="too_large",
                            status=response.status_code,
                            redirects=redirects,
                            size=len(body),
                            detail=f"larger than {max_bytes} bytes",
                        )
                status = response.status_code
                content_type = response.headers.get("content-type")
        except httpx.HTTPError as exc:
            return FetchedFile(
                url=url,
                final_url=current,
                outcome="unreachable",
                redirects=redirects,
                detail=type(exc).__name__,
            )
        raw = bytes(body)
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = _gunzip(raw, max_bytes)
            except ValueError as exc:
                return FetchedFile(
                    url=url,
                    final_url=current,
                    outcome="too_large",
                    status=status,
                    redirects=redirects,
                    detail=str(exc),
                )
        if 400 <= status < 500:
            outcome = "client_error"
        elif status >= 500:
            outcome = "server_error"
        elif 200 <= status < 300:
            outcome = "ok"
        else:
            outcome = "unreachable"
        return FetchedFile(
            url=url,
            final_url=current,
            outcome=outcome,
            status=status,
            content_type=content_type,
            size=len(raw),
            redirects=redirects,
            text=raw.decode("utf-8", errors="replace") if outcome == "ok" else None,
        )
    return FetchedFile(
        url=url, final_url=current, outcome="too_many_redirects", redirects=redirects
    )


def _gunzip(raw: bytes, max_bytes: int) -> bytes:
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = decompressor.decompress(raw, max_bytes + 1)
    except (zlib.error, gzip.BadGzipFile) as exc:
        raise ValueError("invalid gzip data") from exc
    if len(out) > max_bytes or decompressor.unconsumed_tail:
        raise ValueError(f"decompressed size exceeds {max_bytes} bytes")
    return out


# -- robots.txt (RFC 9309) ----------------------------------------------------------


class RobotsRule(BaseModel):
    allow: bool
    path: str


class RobotsGroup(BaseModel):
    agents: list[str]
    rules: list[RobotsRule] = []


class RobotsTxt(BaseModel):
    groups: list[RobotsGroup] = []
    sitemaps: list[str] = []
    invalid_lines: int = 0

    def group_for(self, user_agent: str) -> RobotsGroup | None:
        """The group of the most specific agent token matching ``user_agent``."""
        product = user_agent.lower().split("/")[0].strip()
        merged: dict[str, RobotsGroup] = {}
        for group in self.groups:  # RFC 9309: groups for the same agent are merged
            for agent in group.agents:
                key = agent.lower().strip()
                target = merged.setdefault(key, RobotsGroup(agents=[key]))
                target.rules.extend(group.rules)
        # Case-insensitive match of the product token; otherwise the "*" group.
        return merged.get(product) or merged.get("*")

    def can_fetch(self, user_agent: str, url: str) -> bool:
        group = self.group_for(user_agent)
        if group is None:
            return True
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None
        for rule in group.rules:
            if not rule.path:
                continue  # "Disallow:" with no value allows everything
            if _match(rule.path, target):
                length = len(rule.path)
                if (
                    best is None
                    or length > best[0]
                    or (length == best[0] and rule.allow)
                ):
                    best = (length, rule.allow)
        return True if best is None else best[1]


def _match(pattern: str, target: str) -> bool:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in unquote(body))
    return re.match(regex + ("$" if anchored else ""), unquote(target)) is not None


def parse_robots(text: str) -> RobotsTxt:
    robots = RobotsTxt()
    current: RobotsGroup | None = None
    last_was_agent = False
    for raw_line in text.splitlines()[:20_000]:
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            robots.invalid_lines += 1
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if current is None or not last_was_agent:
                current = RobotsGroup(agents=[])
                robots.groups.append(current)
            current.agents.append(value or "*")
            last_was_agent = True
        elif field in ("allow", "disallow"):
            last_was_agent = False
            if current is None:
                robots.invalid_lines += 1
                continue
            current.rules.append(RobotsRule(allow=field == "allow", path=value))
        elif field == "sitemap":  # not part of any group
            if value:
                robots.sitemaps.append(value)
        # other fields (crawl-delay, host, ...) are ignored and do not end a group
    return robots


class RobotsReport(BaseModel):
    """What the audit observed about robots.txt."""

    file: FetchedFile
    applies: str  # rules | allow_all (4xx) | disallow_all_assumed (5xx/unreachable) | unavailable
    sitemaps: list[str] = []
    googlebot_disallows_root: bool = False
    has_googlebot_group: bool = False
    invalid_lines: int = 0
    parsed: RobotsTxt | None = None

    def allows(self, user_agent: str, url: str) -> bool | None:
        """True/False per the rules, or None when no rules could be applied."""
        if self.applies == "allow_all":
            return True
        if self.applies == "disallow_all_assumed":
            return False
        if self.parsed is None:
            return None
        return self.parsed.can_fetch(user_agent, url)


def analyze_robots(file: FetchedFile) -> RobotsReport:
    if file.outcome == "ok" and file.text is not None:
        parsed = parse_robots(file.text)
        origin = urlsplit(file.final_url or file.url)
        root = f"{origin.scheme}://{origin.netloc}/"
        has_google = any(
            "googlebot" in a.lower() for g in parsed.groups for a in g.agents
        )
        return RobotsReport(
            file=file,
            applies="rules",
            sitemaps=parsed.sitemaps[:50],
            googlebot_disallows_root=not parsed.can_fetch("Googlebot", root),
            has_googlebot_group=has_google,
            invalid_lines=parsed.invalid_lines,
            parsed=parsed,
        )
    if file.outcome == "client_error":
        return RobotsReport(file=file, applies="allow_all")
    if file.outcome in ("server_error", "unreachable", "too_many_redirects"):
        return RobotsReport(file=file, applies="disallow_all_assumed")
    return RobotsReport(file=file, applies="unavailable")


# -- XML sitemaps -------------------------------------------------------------------------


class SitemapReport(BaseModel):
    url: str
    file: FetchedFile
    kind: str  # urlset | sitemapindex | invalid | unavailable
    urls: list[str] = []
    url_count: int = 0
    children: list[str] = []
    lastmod_count: int = 0
    lastmods: dict[str, str] = {}  # loc -> <lastmod> text, for kept URLs only
    error: str | None = None
    truncated: bool = False


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(file: FetchedFile, max_urls: int) -> SitemapReport:
    if file.outcome != "ok" or file.text is None:
        return SitemapReport(
            url=file.url, file=file, kind="unavailable", error=file.outcome
        )
    data = file.text.encode("utf-8", errors="replace")
    if _DECL.search(data):
        return SitemapReport(
            url=file.url,
            file=file,
            kind="invalid",
            error="DOCTYPE/ENTITY declarations are not allowed in sitemaps",
        )
    try:
        # DOCTYPE and ENTITY declarations were rejected above, so no entity
        # expansion can happen; stdlib parsing is therefore safe here.
        root = ElementTree.fromstring(data)  # noqa: S314
    except ElementTree.ParseError as exc:
        return SitemapReport(
            url=file.url, file=file, kind="invalid", error=f"XML parse error: {exc}"
        )
    kind = _local(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        return SitemapReport(
            url=file.url, file=file, kind="invalid", error=f"unexpected root <{kind}>"
        )
    report = SitemapReport(url=file.url, file=file, kind=kind)
    entry = "url" if kind == "urlset" else "sitemap"
    for node in root:
        if _local(node.tag) != entry:
            continue
        loc = next(
            (c.text.strip() for c in node if _local(c.tag) == "loc" and c.text), None
        )
        if not loc:
            continue
        report.url_count += 1
        lastmod = next(
            (c.text.strip() for c in node if _local(c.tag) == "lastmod" and c.text),
            None,
        )
        if lastmod is not None:
            report.lastmod_count += 1
        target = report.urls if kind == "urlset" else report.children
        if len(target) < min(max_urls, MAX_SITEMAP_URLS_KEPT):
            target.append(loc[:2048])
            if lastmod is not None:
                report.lastmods[loc[:2048]] = lastmod[:40]
        else:
            report.truncated = True
    return report
