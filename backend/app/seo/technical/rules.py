"""Technical SEO audit rules: observations -> findings with evidence.

Every finding names its affected URLs and carries evidence items (what was seen,
on which URL, from which source, when, and the HTML hash when it came from
HTML). Rules only read collected data; nothing is inferred from Markdown, and
nothing claims a page is indexed. Thresholds are documented constants. Method:
``seo-toolkit-technical-audit`` (primary) with ``seo-technical``,
``seo-sitemap``, ``seo-hreflang``, ``seo-images`` and ``seo-toolkit-schema``.
Site-wide comparisons (duplicates, hreflang return links) cover the audited
sample only, which the finding text says.
"""

import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any

from app.seo.technical.collector import CollectionResult
from app.seo.technical.html_signals import PageSignals, has_noindex, robots_directives
from app.seo.technical.models import (
    EvidenceItem,
    EvidenceState,
    Finding,
    PageResult,
    Severity,
)
from app.seo.technical.structured_data import agreement_issues

MAX_EVIDENCE = 25
MAX_AFFECTED = 500
TITLE_MAX_CHARS = 60  # beyond this, titles are often truncated in results (heuristic)
TITLE_MIN_CHARS = 10
DESCRIPTION_MAX_CHARS = 160  # heuristic display length
_LANG = re.compile(
    r"^(x-default|[a-z]{2,3}(-[a-z]{4})?(-([a-z]{2}|\d{3}))?)$", re.IGNORECASE
)

TECH = "seo-toolkit-technical-audit"


def _evidence(page: PageResult, source: str, **observed: Any) -> EvidenceItem:
    return EvidenceItem(
        url=page.url,
        source=source,
        observed=observed,
        observed_at=page.fetched_at,
        page_id=page.id,
        html_sha256=page.html_sha256 if source == "firecrawl_raw_html" else None,
    )


def _finding(
    check_id: str,
    category: str,
    severity: Severity,
    title: str,
    detail: str,
    evidence: list[EvidenceItem],
    *,
    refs: Iterable[str] = (TECH,),
    state: EvidenceState = "observed",
    affected: list[str] | None = None,
) -> Finding:
    urls = (
        affected
        if affected is not None
        else list(dict.fromkeys(e.url for e in evidence))
    )
    return Finding(
        check_id=check_id,
        category=category,
        severity=severity,
        title=title,
        detail=detail,
        affected_urls=urls[:MAX_AFFECTED],
        affected_count=len(urls),
        evidence=evidence[:MAX_EVIDENCE],
        evidence_state=state,
        skill_refs=list(refs),
    )


def _sig(page: PageResult) -> PageSignals:
    """Signals of a parsed page (rules call this only for pages with HTML)."""
    if page.signals is None:
        raise ValueError("rule called for a page without parsed HTML")
    return page.signals


def _html_pages(result: CollectionResult) -> list[PageResult]:
    return [p for p in result.pages if p.signals is not None]


def _is_home(page: PageResult, result: CollectionResult) -> bool:
    return page.url == result.start_url


# -- crawl access: robots.txt and sitemaps -----------------------------------------


def robots_rules(result: CollectionResult) -> list[Finding]:
    robots = result.robots
    if robots is None:
        return []
    file_ev = EvidenceItem(
        url=robots.file.url,
        source="robots_txt",
        observed={
            "outcome": robots.file.outcome,
            "status": robots.file.status,
            "applies": robots.applies,
        },
    )
    out: list[Finding] = []
    if robots.applies == "disallow_all_assumed":
        out.append(
            _finding(
                "robots.unavailable",
                "crawlability",
                "high",
                "robots.txt could not be retrieved (server error or unreachable)",
                "When robots.txt answers 5xx or cannot be reached, crawlers following RFC 9309 "
                "(including Google) treat the whole site as disallowed until it is reachable again.",
                [file_ev],
            )
        )
    if robots.googlebot_disallows_root:
        out.append(
            _finding(
                "robots.disallows_root",
                "crawlability",
                "high",
                "robots.txt disallows the site root for Googlebot",
                "The rule group that applies to Googlebot blocks '/'.",
                [file_ev],
            )
        )
    blocked = [p for p in result.pages if p.googlebot_allowed is False]
    if blocked and robots.applies == "rules":
        sev: Severity = (
            "high" if any(_is_home(p, result) for p in blocked) else "medium"
        )
        out.append(
            _finding(
                "robots.blocks_pages",
                "crawlability",
                sev,
                "Audited pages are disallowed for Googlebot by robots.txt",
                "These URLs were linked from the site or listed in a sitemap but robots.txt "
                "disallows them for Googlebot. Disallowed URLs can still be indexed without "
                "content if linked; use noindex (on a crawlable page) to keep them out.",
                [
                    _evidence(
                        p, "robots_txt", googlebot_allowed=False, via=p.discovered_via
                    )
                    for p in blocked
                ],
            )
        )
    if robots.applies == "rules" and not robots.sitemaps:
        out.append(
            _finding(
                "robots.no_sitemap_reference",
                "crawlability",
                "info",
                "robots.txt does not reference a sitemap",
                "A 'Sitemap:' line helps crawlers find the sitemap; it is optional.",
                [file_ev],
                refs=(TECH, "seo-sitemap"),
            )
        )
    return out


def sitemap_rules(result: CollectionResult) -> list[Finding]:
    out: list[Finding] = []
    reports = result.sitemaps
    if not any(r.kind in ("urlset", "sitemapindex") for r in reports):
        ev = [
            EvidenceItem(
                url=r.url,
                source="sitemap",
                observed={
                    "outcome": r.file.outcome,
                    "status": r.file.status,
                    "error": r.error,
                },
            )
            for r in reports
        ]
        out.append(
            _finding(
                "sitemap.missing",
                "crawlability",
                "low",
                "No valid XML sitemap was found",
                "Checked the robots.txt 'Sitemap:' entries or /sitemap.xml. A sitemap is optional "
                "but helps discovery of pages that are not well linked.",
                ev,
                refs=(TECH, "seo-sitemap"),
                affected=[r.url for r in reports],
            )
        )
    invalid = [
        r for r in reports if r.kind == "invalid" or r.file.outcome == "too_large"
    ]
    if invalid:
        out.append(
            _finding(
                "sitemap.invalid",
                "crawlability",
                "medium",
                "A sitemap could not be parsed",
                "The file is not a valid sitemap (XML error, unexpected root, size limit or a "
                "forbidden DOCTYPE/ENTITY declaration).",
                [
                    EvidenceItem(
                        url=r.url,
                        source="sitemap",
                        observed={"error": r.error or r.file.detail},
                    )
                    for r in invalid
                ],
                refs=(TECH, "seo-sitemap"),
            )
        )
    listed = {u for r in reports for u in r.urls}
    bad_status, noindex, non_canonical = [], [], []
    for page in result.pages:
        if page.url not in listed and (page.final_url or "") not in listed:
            continue
        if page.status_code is not None and (
            len(page.redirect_chain) > 1 or not 200 <= page.status_code < 300
        ):
            bad_status.append(
                _evidence(
                    page,
                    "http_trace",
                    status=page.status_code,
                    redirects=len(page.redirect_chain) - 1,
                    final_url=page.final_url,
                )
            )
        if "noindex" in page.indexability.reasons:
            noindex.append(_evidence(page, "firecrawl_raw_html", noindex=True))
        if "canonical_points_elsewhere" in page.indexability.reasons and page.signals:
            non_canonical.append(
                _evidence(
                    page, "firecrawl_raw_html", canonical=_sig(page).canonicals[0]
                )
            )
    if bad_status:
        out.append(
            _finding(
                "sitemap.lists_non_200",
                "crawlability",
                "medium",
                "Sitemap lists URLs that redirect or do not return 2xx",
                "Sitemaps should list final, 200-status URLs. Checked for the audited URLs only.",
                bad_status,
                refs=(TECH, "seo-sitemap"),
            )
        )
    if noindex:
        out.append(
            _finding(
                "sitemap.lists_noindex",
                "indexability",
                "medium",
                "Sitemap lists noindex pages",
                "Listing a URL in a sitemap while marking it noindex sends conflicting signals.",
                noindex,
                refs=(TECH, "seo-sitemap"),
            )
        )
    if non_canonical:
        out.append(
            _finding(
                "sitemap.lists_non_canonical",
                "indexability",
                "low",
                "Sitemap lists pages whose canonical points elsewhere",
                "Sitemaps should list canonical URLs.",
                non_canonical,
                refs=(TECH, "seo-sitemap"),
            )
        )
    return out


# -- HTTP status, redirects, indexability ------------------------------------------


def status_rules(result: CollectionResult) -> list[Finding]:
    out: list[Finding] = []
    errors = [
        p for p in result.pages if p.status_code is not None and p.status_code >= 400
    ]
    groups: tuple[tuple[tuple[int, int], Severity, str], ...] = (
        ((500, 600), "high", "Pages return server errors (5xx)"),
        ((400, 500), "medium", "Pages return client errors (4xx)"),
    )
    for code_range, sev, title in groups:
        hit = [
            p for p in errors if code_range[0] <= (p.status_code or 0) < code_range[1]
        ]
        if hit:
            out.append(
                _finding(
                    f"status.{code_range[0] // 100}xx",
                    "status",
                    sev,
                    title,
                    "Status as observed by AURORA's HTTP trace (or reported by Firecrawl when the "
                    "trace failed; see 'source').",
                    [
                        _evidence(
                            p,
                            p.status_source,
                            status=p.status_code,
                            via=p.discovered_via,
                        )
                        for p in hit
                    ],
                )
            )
    chains = [p for p in result.pages if len(p.redirect_chain) > 2]
    if chains:
        out.append(
            _finding(
                "redirect.chain",
                "status",
                "low",
                "Redirect chains (more than one hop)",
                "Each extra hop adds latency and can dilute signals; link to the final URL.",
                [
                    _evidence(
                        p, "http_trace", hops=[h.model_dump() for h in p.redirect_chain]
                    )
                    for p in chains
                ],
            )
        )
    loops = [p for p in result.pages if p.fetch_status == "redirect_loop"]
    if loops:
        out.append(
            _finding(
                "redirect.loop_or_too_long",
                "status",
                "medium",
                "Redirects loop or exceed the hop limit",
                "The trace stopped after the maximum number of redirects.",
                [_evidence(p, "http_trace", detail=p.fetch_detail) for p in loops],
            )
        )
    temporary = [
        p
        for p in result.pages
        if p.redirect_chain and p.redirect_chain[0].status in (302, 303, 307)
    ]
    if temporary:
        out.append(
            _finding(
                "redirect.temporary",
                "status",
                "info",
                "Temporary redirects (302/303/307)",
                "Fine for genuinely temporary moves; permanent moves should use 301/308.",
                [
                    _evidence(
                        p,
                        "http_trace",
                        status=p.redirect_chain[0].status,
                        to=p.final_url,
                    )
                    for p in temporary
                ],
            )
        )
    http_only = [p for p in result.pages if (p.final_url or "").startswith("http://")]
    if http_only:
        out.append(
            _finding(
                "security.http_final_url",
                "security",
                "medium",
                "Pages are served over plain HTTP",
                "The final URL after redirects is not HTTPS.",
                [_evidence(p, "http_trace", final_url=p.final_url) for p in http_only],
            )
        )
    return out


def indexability_rules(result: CollectionResult) -> list[Finding]:
    out: list[Finding] = []
    noindex = []
    for page in _html_pages(result):
        directives = robots_directives(
            _sig(page),
            [page.headers["x-robots-tag"]] if "x-robots-tag" in page.headers else [],
        )
        if has_noindex(directives):
            source = (
                "http_trace"
                if any(t in ("noindex", "none") for t in directives["header"])
                else "firecrawl_raw_html"
            )
            noindex.append(
                (
                    page,
                    _evidence(
                        page,
                        source,
                        meta_robots=directives["meta"],
                        x_robots_tag=directives["header"],
                    ),
                )
            )
    if noindex:
        sev: Severity = (
            "high" if any(_is_home(p, result) for p, _ in noindex) else "medium"
        )
        out.append(
            _finding(
                "indexability.noindex",
                "indexability",
                sev,
                "Pages are marked noindex (meta robots or X-Robots-Tag)",
                "noindex removes a page from search results once crawled. Confirm each one is "
                "intended.",
                [e for _, e in noindex],
            )
        )
    refresh = [p for p in _html_pages(result) if p.signals and _sig(p).meta_refresh]
    if refresh:
        out.append(
            _finding(
                "indexability.meta_refresh",
                "indexability",
                "low",
                "Meta refresh redirects",
                "Server-side 301/308 redirects are clearer than <meta http-equiv=refresh>.",
                [
                    _evidence(
                        p, "firecrawl_raw_html", meta_refresh=_sig(p).meta_refresh
                    )
                    for p in refresh
                ],
            )
        )
    return out


# -- on-page metadata ----------------------------------------------------------------


def _duplicates(
    pages: list[PageResult], key: Callable[[PageResult], str | None]
) -> dict[str, list[PageResult]]:
    groups: dict[str, list[PageResult]] = defaultdict(list)
    for page in pages:
        if page.status_code is not None and not 200 <= page.status_code < 300:
            continue
        value = key(page)
        if value:
            groups[value.strip().lower()].append(page)
    return {k: v for k, v in groups.items() if len(v) > 1}


def metadata_rules(result: CollectionResult) -> list[Finding]:
    pages = _html_pages(result)
    out: list[Finding] = []

    def first(values: list[str]) -> str | None:
        return next((v for v in values if v.strip()), None)

    missing_title = [p for p in pages if not first(_sig(p).titles)]
    if missing_title:
        out.append(
            _finding(
                "title.missing",
                "metadata",
                "medium",
                "Pages without a <title>",
                "The title is the main source of the result headline.",
                [
                    _evidence(p, "firecrawl_raw_html", titles=_sig(p).titles)
                    for p in missing_title
                ],
            )
        )
    multi_title = [
        p for p in pages if len([t for t in _sig(p).titles if t.strip()]) > 1
    ]
    if multi_title:
        out.append(
            _finding(
                "title.multiple",
                "metadata",
                "low",
                "Pages with more than one <title>",
                "Only one title element should exist per page.",
                [
                    _evidence(p, "firecrawl_raw_html", titles=_sig(p).titles)
                    for p in multi_title
                ],
            )
        )
    duplicate_checks: tuple[
        tuple[Callable[[PageResult], str | None], str, str, Severity], ...
    ] = (
        (lambda p: first(_sig(p).titles), "title.duplicate", "title", "medium"),
        (
            lambda p: first(_sig(p).meta_descriptions),
            "description.duplicate",
            "meta description",
            "low",
        ),
    )
    for group_key, check_id, label, sev in duplicate_checks:
        dupes = _duplicates(pages, group_key)
        if dupes:
            ev = [
                _evidence(p, "firecrawl_raw_html", value=value, shared_by=len(group))
                for value, group in dupes.items()
                for p in group
            ]
            out.append(
                _finding(
                    check_id,
                    "metadata",
                    sev,
                    f"Duplicate {label}s across pages",
                    f"{len(dupes)} {label}(s) are shared by more than one audited page "
                    "(within the audited sample).",
                    ev,
                )
            )
    lengths = []
    for p in pages:
        title = first(_sig(p).titles)
        if title and (len(title) > TITLE_MAX_CHARS or len(title) < TITLE_MIN_CHARS):
            lengths.append(
                _evidence(p, "firecrawl_raw_html", title=title, length=len(title))
            )
    if lengths:
        out.append(
            _finding(
                "title.length",
                "metadata",
                "info",
                "Titles outside the typical display length",
                f"Shorter than {TITLE_MIN_CHARS} or longer than {TITLE_MAX_CHARS} characters. This is a "
                "display heuristic, not a ranking rule; search engines may rewrite titles.",
                lengths,
            )
        )
    missing_desc = [p for p in pages if not first(_sig(p).meta_descriptions)]
    if missing_desc:
        out.append(
            _finding(
                "description.missing",
                "metadata",
                "low",
                "Pages without a meta description",
                "Without one, search engines build the snippet from page text.",
                [_evidence(p, "firecrawl_raw_html") for p in missing_desc],
            )
        )
    multi_desc = [p for p in pages if len(_sig(p).meta_descriptions) > 1]
    if multi_desc:
        out.append(
            _finding(
                "description.multiple",
                "metadata",
                "low",
                "Pages with several meta descriptions",
                "Only one should exist.",
                [
                    _evidence(
                        p, "firecrawl_raw_html", count=len(_sig(p).meta_descriptions)
                    )
                    for p in multi_desc
                ],
            )
        )
    no_h1 = [p for p in pages if _sig(p).h1_count == 0]
    if no_h1:
        out.append(
            _finding(
                "headings.h1_missing",
                "content_structure",
                "low",
                "Pages without an H1",
                "A single descriptive H1 helps users and search engines understand the topic.",
                [_evidence(p, "firecrawl_raw_html", h1_count=0) for p in no_h1],
            )
        )
    many_h1 = [p for p in pages if _sig(p).h1_count > 1]
    if many_h1:
        out.append(
            _finding(
                "headings.h1_multiple",
                "content_structure",
                "info",
                "Pages with several H1s",
                "Valid HTML; a single H1 is usually clearer.",
                [
                    _evidence(p, "firecrawl_raw_html", h1_count=_sig(p).h1_count)
                    for p in many_h1
                ],
            )
        )
    alt = [p for p in pages if _sig(p).images.missing_alt > 0]
    if alt:
        out.append(
            _finding(
                "images.alt_missing",
                "accessibility",
                "low",
                "Images without an alt attribute",
                'Images need alt text (or alt="" when decorative) for accessibility and image search.',
                [
                    _evidence(
                        p,
                        "firecrawl_raw_html",
                        missing=_sig(p).images.missing_alt,
                        total=_sig(p).images.total,
                        samples=_sig(p).images.missing_alt_samples[:5],
                    )
                    for p in alt
                ],
                refs=(TECH, "seo-images"),
            )
        )
    no_lang = [p for p in pages if not _sig(p).html_lang]
    if no_lang:
        out.append(
            _finding(
                "html.lang_missing",
                "metadata",
                "low",
                "Pages without <html lang>",
                "The lang attribute declares the page language.",
                [_evidence(p, "firecrawl_raw_html") for p in no_lang],
            )
        )
    no_viewport = [p for p in pages if not _sig(p).viewport]
    if no_viewport:
        out.append(
            _finding(
                "html.viewport_missing",
                "mobile",
                "medium",
                "Pages without a viewport meta tag",
                "Without <meta name=viewport>, mobile browsers render a desktop-width page.",
                [_evidence(p, "firecrawl_raw_html") for p in no_viewport],
            )
        )
    mixed = [p for p in pages if _sig(p).mixed_content]
    if mixed:
        out.append(
            _finding(
                "security.mixed_content",
                "security",
                "medium",
                "HTTPS pages load HTTP resources",
                "Browsers block or warn about insecure subresources on HTTPS pages.",
                [
                    _evidence(
                        p, "firecrawl_raw_html", samples=_sig(p).mixed_content[:5]
                    )
                    for p in mixed
                ],
            )
        )
    return out


# -- canonical -------------------------------------------------------------------------


def canonical_rules(result: CollectionResult) -> list[Finding]:
    pages = [
        p
        for p in _html_pages(result)
        if p.status_code is None or 200 <= p.status_code < 300
    ]
    by_url = {p.url: p for p in result.pages} | {
        p.final_url: p for p in result.pages if p.final_url
    }
    out: list[Finding] = []
    missing = [
        p for p in pages if not _sig(p).canonicals and not _sig(p).canonical_invalid
    ]
    if missing:
        out.append(
            _finding(
                "canonical.missing",
                "canonical",
                "low",
                "Pages without a canonical link",
                "A self-referencing rel=canonical makes the preferred URL explicit.",
                [_evidence(p, "firecrawl_raw_html") for p in missing],
            )
        )
    conflicting = [p for p in pages if len(set(_sig(p).canonicals)) > 1]
    if conflicting:
        out.append(
            _finding(
                "canonical.conflicting",
                "canonical",
                "medium",
                "Pages declare several different canonical URLs",
                "Conflicting canonicals are typically ignored.",
                [
                    _evidence(p, "firecrawl_raw_html", canonicals=_sig(p).canonicals)
                    for p in conflicting
                ],
            )
        )
    invalid = [p for p in pages if _sig(p).canonical_invalid]
    if invalid:
        out.append(
            _finding(
                "canonical.invalid",
                "canonical",
                "medium",
                "Canonical links that are not valid URLs",
                "The href could not be resolved to an http(s) URL.",
                [
                    _evidence(p, "firecrawl_raw_html", values=_sig(p).canonical_invalid)
                    for p in invalid
                ],
            )
        )
    elsewhere, broken_target = [], []
    header_canonical = []
    for p in pages:
        link_header = p.headers.get("link", "")
        if (
            'rel="canonical"' in link_header.lower()
            or "rel=canonical" in link_header.lower()
        ):
            header_canonical.append(
                _evidence(p, "http_trace", link_header=link_header[:300])
            )
        if "canonical_points_elsewhere" in p.indexability.reasons:
            target = _sig(p).canonicals[0]
            known = by_url.get(target)
            elsewhere.append(_evidence(p, "firecrawl_raw_html", canonical=target))
            if (
                known
                and known.status_code is not None
                and (
                    not 200 <= known.status_code < 300 or len(known.redirect_chain) > 1
                )
            ):
                broken_target.append(
                    _evidence(
                        p,
                        "firecrawl_raw_html",
                        canonical=target,
                        target_status=known.status_code,
                        target_redirects=len(known.redirect_chain) - 1,
                    )
                )
    if elsewhere:
        out.append(
            _finding(
                "canonical.points_elsewhere",
                "canonical",
                "info",
                "Pages canonicalize to another URL",
                "Often intended (parameters, duplicates). Confirm each target is the preferred page.",
                elsewhere,
            )
        )
    if broken_target:
        out.append(
            _finding(
                "canonical.target_not_200",
                "canonical",
                "medium",
                "Canonical targets redirect or do not return 2xx",
                "A canonical should point to a final, 200-status URL (checked for audited targets).",
                broken_target,
            )
        )
    if header_canonical:
        out.append(
            _finding(
                "canonical.http_header",
                "canonical",
                "info",
                "Canonical declared in the HTTP Link header",
                "Valid; make sure it agrees with any HTML canonical.",
                header_canonical,
            )
        )
    return out


# -- hreflang ----------------------------------------------------------------------------


def hreflang_rules(result: CollectionResult) -> list[Finding]:
    pages = [p for p in _html_pages(result) if _sig(p).hreflang]
    if not pages:
        return []
    out: list[Finding] = []
    refs = (TECH, "seo-hreflang")
    invalid = [
        p for p in pages if any(not _LANG.match(h.lang) for h in _sig(p).hreflang)
    ]
    if invalid:
        out.append(
            _finding(
                "hreflang.invalid_code",
                "international",
                "low",
                "Invalid hreflang values",
                "Values must be a language code (ISO 639-1), optionally with a region, or x-default.",
                [
                    _evidence(
                        p,
                        "firecrawl_raw_html",
                        invalid=[
                            h.lang for h in _sig(p).hreflang if not _LANG.match(h.lang)
                        ],
                    )
                    for p in invalid
                ],
                refs=refs,
            )
        )
    no_self = [
        p
        for p in pages
        if (p.final_url or p.url) not in {h.href for h in _sig(p).hreflang}
    ]
    if no_self:
        out.append(
            _finding(
                "hreflang.missing_self",
                "international",
                "low",
                "hreflang sets without a self-reference",
                "Each page in an hreflang set should list itself.",
                [_evidence(p, "firecrawl_raw_html") for p in no_self],
                refs=refs,
            )
        )
    by_url = {(p.final_url or p.url): p for p in pages}
    missing_return = []
    for page in pages:
        me = page.final_url or page.url
        for alt in _sig(page).hreflang:
            other = by_url.get(alt.href)
            if (
                other is not None
                and other is not page
                and me not in {h.href for h in _sig(other).hreflang}
            ):
                missing_return.append(
                    _evidence(
                        page, "firecrawl_raw_html", alternate=alt.href, lang=alt.lang
                    )
                )
    if missing_return:
        out.append(
            _finding(
                "hreflang.no_return_link",
                "international",
                "low",
                "hreflang alternates that do not link back",
                "hreflang must be reciprocal; checked only between audited pages.",
                missing_return,
                refs=refs,
                state="inferred",
            )
        )
    return out


# -- structured data -------------------------------------------------------------------------


def structured_data_rules(result: CollectionResult) -> list[Finding]:
    refs = ("seo-toolkit-schema", "seo-schema")
    syntax: list[EvidenceItem] = []
    feature: list[EvidenceItem] = []
    vocabulary: list[EvidenceItem] = []
    retired: list[EvidenceItem] = []
    agreement: list[EvidenceItem] = []
    for page in _html_pages(result):
        report = page.structured_data
        if report is None:
            continue
        for issue in report.issues:
            syntax.append(_evidence(page, "firecrawl_raw_html", message=issue.message))
        for item in report.items:
            for issue in item.issues:
                ev = _evidence(
                    page,
                    "firecrawl_raw_html",
                    type=issue.type or ",".join(item.types),
                    property=issue.property,
                    message=issue.message,
                    level=issue.level,
                )
                {
                    "feature": feature,
                    "vocabulary": vocabulary,
                    "deprecated": retired,
                }.get(issue.question, feature).append(ev)
        h1s = [h.text for h in _sig(page).headings if h.level == 1]
        for issue in agreement_issues(report, _sig(page).titles, h1s):
            agreement.append(
                _evidence(page, "firecrawl_raw_html", message=issue.message)
            )
    out: list[Finding] = []
    if syntax:
        out.append(
            _finding(
                "schema.parse_error",
                "structured_data",
                "medium",
                "JSON-LD that does not parse",
                "Invalid JSON-LD is ignored by consumers.",
                syntax,
                refs=refs,
            )
        )
    if vocabulary:
        out.append(
            _finding(
                "schema.vocabulary",
                "structured_data",
                "low",
                "Structured data without schema.org context or @type",
                "Items need a schema.org @context and an @type to be understood.",
                vocabulary,
                refs=refs,
            )
        )
    required = [e for e in feature if e.observed.get("level") == "warning"]
    recommended = [e for e in feature if e.observed.get("level") == "info"]
    if required:
        out.append(
            _finding(
                "schema.missing_required",
                "structured_data",
                "low",
                "Structured data missing properties required by search features",
                "Per AURORA's documented rule set; confirm in Google's Rich Results Test. Meeting "
                "requirements never guarantees a rich result, and missing facts must not be invented.",
                required,
                refs=refs,
            )
        )
    if recommended:
        out.append(
            _finding(
                "schema.missing_recommended",
                "structured_data",
                "info",
                "Structured data missing recommended properties",
                "Add only when the fact is visible on the page.",
                recommended,
                refs=refs,
            )
        )
    if retired:
        out.append(
            _finding(
                "schema.retired_feature",
                "structured_data",
                "info",
                "Markup for retired rich-result features",
                "These types no longer produce Google rich results; no removal is required.",
                retired,
                refs=refs,
            )
        )
    if agreement:
        out.append(
            _finding(
                "schema.disagrees_with_page",
                "structured_data",
                "info",
                "Marked-up headline not visible in title or H1",
                "Structured data must describe what is on the page.",
                agreement,
                refs=refs,
                state="inferred",
            )
        )
    return out


# -- internal links --------------------------------------------------------------------------


def link_rules(result: CollectionResult) -> list[Finding]:
    out: list[Finding] = []
    broken = [c for c in result.link_checks if c.outcome == "broken"]
    if broken:
        ev = [
            EvidenceItem(
                url=c.url,
                source="link_check",
                observed={
                    "status": c.status,
                    "linked_from": c.referrers[:10],
                    "anchors": c.anchors,
                },
            )
            for c in broken
        ]
        out.append(
            _finding(
                "links.broken_internal",
                "links",
                "medium",
                "Broken internal links (4xx/5xx targets)",
                "Internal links point to URLs that return an error.",
                ev,
                refs=(TECH, "seo-toolkit-internal-linking"),
                affected=sorted({r for c in broken for r in c.referrers}),
            )
        )
    redirecting = [c for c in result.link_checks if c.outcome == "redirect"]
    if redirecting:
        ev = [
            EvidenceItem(
                url=c.url,
                source="link_check",
                observed={
                    "final_url": c.final_url,
                    "redirects": c.redirect_count,
                    "linked_from": c.referrers[:10],
                },
            )
            for c in redirecting
        ]
        out.append(
            _finding(
                "links.redirecting_internal",
                "links",
                "info",
                "Internal links that redirect",
                "Linking to the final URL avoids a hop.",
                ev,
                refs=(TECH, "seo-toolkit-internal-linking"),
                affected=sorted({r for c in redirecting for r in c.referrers}),
            )
        )
    return out


ALL_RULES = (
    robots_rules,
    sitemap_rules,
    status_rules,
    indexability_rules,
    metadata_rules,
    canonical_rules,
    hreflang_rules,
    structured_data_rules,
    link_rules,
)


def run_rules(result: CollectionResult) -> list[Finding]:
    findings: list[Finding] = []
    for rule in ALL_RULES:
        findings.extend(rule(result))
    return findings
