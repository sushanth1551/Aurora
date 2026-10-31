"""HTML signals, robots directives, robots.txt (RFC 9309), sitemaps and JSON-LD."""

import gzip
import json

import pytest

from app.seo.technical.html_signals import has_noindex, parse_html, robots_directives
from app.seo.technical.site_files import (
    FetchedFile,
    analyze_robots,
    parse_robots,
    parse_sitemap,
)
from app.seo.technical.structured_data import agreement_issues, analyze_json_ld

URL = "https://www.example.com/blog/post"

HTML = """<!doctype html><html lang="en-GB"><head>
<meta charset="utf-8"><title> Post  title </title>
<meta name="description" content="About the post">
<meta name="robots" content="index, Follow"><meta name="googlebot" content="noarchive">
<meta name="viewport" content="width=device-width">
<link rel="canonical" href="/blog/post">
<link rel="alternate" hreflang="de" href="https://www.example.com/de/post">
<link rel="stylesheet" href="http://cdn.example.net/a.css">
<meta property="og:title" content="OG title">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Article","headline":"Post title"}</script>
</head><body>
<nav><a href="/">Home</a><a href="https://example.com/about">About</a></nav>
<h1>Post title</h1><h2>Section</h2><h1>Second</h1>
<p><a href="/blog/other#frag" rel="nofollow sponsored">Other</a>
<a href="https://external.org/x">Ext</a><a href="mailto:a@b.c">mail</a><a href="javascript:void(0)">js</a></p>
<img src="/a.png"><img src="/b.png" alt=""><img src="/c.png" alt="C">
<img src="http://insecure.example.com/i.png" alt="x">
<script>var hidden = "words that are not visible";</script>
</body></html>"""


def test_head_signals_are_extracted_from_raw_html() -> None:
    s = parse_html(HTML, URL, "example.com")
    assert s.titles == ["Post title"]
    assert s.meta_descriptions == ["About the post"]
    assert s.robots_meta == {"robots": ["index", "follow"], "googlebot": ["noarchive"]}
    assert s.canonicals == [
        "https://www.example.com/blog/post"
    ]  # resolved against the page URL
    assert [(h.lang, h.href) for h in s.hreflang] == [
        ("de", "https://www.example.com/de/post")
    ]
    assert (
        s.html_lang == "en-GB"
        and s.viewport == "width=device-width"
        and s.charset == "utf-8"
    )
    assert s.open_graph == {"og:title": "OG title"}
    assert len(s.json_ld_blocks) == 1


def test_body_signals_headings_images_links() -> None:
    s = parse_html(HTML, URL, "example.com")
    assert [(h.level, h.text) for h in s.headings] == [
        (1, "Post title"),
        (2, "Section"),
        (1, "Second"),
    ]
    assert s.h1_count == 2
    assert s.images.total == 4 and s.images.missing_alt == 1 and s.images.empty_alt == 1
    assert s.images.missing_alt_samples == ["https://www.example.com/a.png"]
    hrefs = {link.href: link for link in s.links}
    assert set(hrefs) == {
        "https://www.example.com/",
        "https://example.com/about",
        "https://www.example.com/blog/other",
        "https://external.org/x",
    }
    assert hrefs[
        "https://example.com/about"
    ].internal  # www and bare host are the same site
    assert not hrefs["https://external.org/x"].internal
    assert hrefs["https://www.example.com/"].placement == "navigation"
    other = hrefs["https://www.example.com/blog/other"]
    assert (
        other.nofollow
        and other.placement == "content"
        and other.rel == ["nofollow", "sponsored"]
    )
    assert s.mixed_content == [
        "http://insecure.example.com/i.png",
        "http://cdn.example.net/a.css",
    ]
    assert s.word_count > 0 and "hidden" not in " ".join(h.text for h in s.headings)


def test_base_href_changes_resolution_and_invalid_canonical_is_kept_apart() -> None:
    html = (
        '<html><head><base href="https://example.com/sub/"><link rel="canonical" href="page">'
        '<link rel="canonical" href="javascript:x"></head><body><a href="x">x</a></body></html>'
    )
    s = parse_html(html, "https://example.com/", "example.com")
    assert s.base_href == "https://example.com/sub/"
    assert s.canonicals == ["https://example.com/sub/page"]
    assert s.canonical_invalid == ["javascript:x"]
    assert s.links[0].href == "https://example.com/sub/x"


def test_missing_metadata_is_reported_as_absent_not_guessed() -> None:
    s = parse_html(
        "<html><body><p>Only text</p></body></html>",
        "https://example.com/",
        "example.com",
    )
    assert s.titles == [] and s.meta_descriptions == [] and s.canonicals == []
    assert s.html_lang is None and s.viewport is None and s.h1_count == 0


@pytest.mark.parametrize(
    ("meta", "header", "noindex"),
    [
        ("noindex, follow", [], True),
        ("none", [], True),
        ("index", ["noindex"], True),
        ("index", ["googlebot: noindex"], True),
        ("index", ["otherbot: noindex"], False),  # scoped to another crawler
        ("index", ["max-snippet: 0, unavailable_after: 2030-01-01"], False),
        ("index, follow", [], False),
    ],
)
def test_noindex_from_meta_and_x_robots_tag(
    meta: str, header: list[str], noindex: bool
) -> None:
    s = parse_html(
        f'<html><head><meta name="robots" content="{meta}"></head></html>',
        URL,
        "example.com",
    )
    assert has_noindex(robots_directives(s, header)) is noindex


def test_googlebot_meta_counts_but_bingbot_meta_does_not() -> None:
    google = parse_html('<meta name="googlebot" content="noindex">', URL, "example.com")
    bing = parse_html('<meta name="bingbot" content="noindex">', URL, "example.com")
    assert has_noindex(robots_directives(google, []))
    assert not has_noindex(robots_directives(bing, []))


# -- robots.txt -----------------------------------------------------------------------------

ROBOTS = """# comment
User-agent: *
Disallow: /private/
Allow: /private/public$
Disallow: /*.pdf$
Disallow: /search?

User-agent: Googlebot
User-agent: Other
Disallow: /no-google/
Allow: /no-google/yes
Crawl-delay: 5
Disallow: /also/

Sitemap: https://example.com/sitemap.xml
"""


@pytest.mark.parametrize(
    ("agent", "path", "allowed"),
    [
        ("AuroraResearchBot", "/private/x", False),
        ("AuroraResearchBot", "/private/public", True),  # longest match wins
        ("AuroraResearchBot", "/private/public/more", False),  # $ anchors the end
        ("AuroraResearchBot", "/files/a.pdf", False),  # * wildcard
        ("AuroraResearchBot", "/files/a.pdf?x=1", True),
        ("AuroraResearchBot", "/search?q=1", False),
        ("Googlebot", "/private/x", True),  # Googlebot has its own group
        ("Googlebot", "/no-google/a", False),
        ("Googlebot", "/no-google/yes", True),
        ("Googlebot", "/also/x", False),  # crawl-delay does not end the group
        ("googlebot/2.1", "/no-google/a", False),  # case-insensitive product token
    ],
)
def test_robots_rfc9309_matching(agent: str, path: str, allowed: bool) -> None:
    robots = parse_robots(ROBOTS)
    assert robots.can_fetch(agent, f"https://example.com{path}") is allowed
    assert robots.sitemaps == ["https://example.com/sitemap.xml"]


def test_robots_allow_wins_a_tie_and_empty_disallow_allows() -> None:
    robots = parse_robots("User-agent: *\nDisallow: /page\nAllow: /page\nDisallow:\n")
    assert robots.can_fetch("x", "https://example.com/page")


@pytest.mark.parametrize(
    ("outcome", "status", "applies", "allows"),
    [
        ("client_error", 404, "allow_all", True),  # RFC 9309: 4xx = no restrictions
        (
            "server_error",
            503,
            "disallow_all_assumed",
            False,
        ),  # 5xx = assume full disallow
        ("unreachable", None, "disallow_all_assumed", False),
        ("blocked", None, "unavailable", None),
    ],
)
def test_robots_status_semantics(outcome, status, applies, allows) -> None:
    report = analyze_robots(
        FetchedFile(
            url="https://example.com/robots.txt", outcome=outcome, status=status
        )
    )
    assert report.applies == applies
    assert report.allows("Googlebot", "https://example.com/") is allows


def test_robots_root_disallow_for_googlebot_is_detected() -> None:
    file = FetchedFile(
        url="https://example.com/robots.txt",
        outcome="ok",
        status=200,
        text="User-agent: Googlebot\nDisallow: /\n",
    )
    report = analyze_robots(file)
    assert report.googlebot_disallows_root and report.has_googlebot_group


# -- sitemaps ---------------------------------------------------------------------------------


def _file(text: str) -> FetchedFile:
    return FetchedFile(
        url="https://example.com/sitemap.xml", outcome="ok", status=200, text=text
    )


def test_urlset_and_sitemap_index() -> None:
    urlset = parse_sitemap(
        _file(
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc> https://example.com/a </loc><lastmod>2026-01-01</lastmod></url>"
            "<url><loc>https://example.com/b</loc></url><url></url></urlset>"
        ),
        100,
    )
    assert urlset.kind == "urlset" and urlset.urls == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert urlset.url_count == 2 and urlset.lastmod_count == 1
    index = parse_sitemap(
        _file(
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<sitemap><loc>https://example.com/s1.xml</loc></sitemap></sitemapindex>"
        ),
        100,
    )
    assert index.kind == "sitemapindex" and index.children == [
        "https://example.com/s1.xml"
    ]


@pytest.mark.parametrize(
    "text",
    [
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><urlset>&lol;</urlset>',
        "<urlset><url><loc>x</loc></url>",  # broken XML
        "<html><body>not a sitemap</body></html>",
    ],
)
def test_invalid_or_hostile_sitemaps_are_rejected(text: str) -> None:
    report = parse_sitemap(_file(text), 100)
    assert report.kind == "invalid" and report.error and report.urls == []


def test_sitemap_url_cap_marks_truncation() -> None:
    entries = "".join(
        f"<url><loc>https://example.com/{i}</loc></url>" for i in range(10)
    )
    report = parse_sitemap(_file(f"<urlset>{entries}</urlset>"), 3)
    assert report.url_count == 10 and len(report.urls) == 3 and report.truncated


def test_gzip_payload_is_supported_by_the_fetcher_format() -> None:
    # The fetcher gunzips bodies starting with the gzip magic bytes (see test_seo_fetching).
    assert gzip.decompress(gzip.compress(b"<urlset/>")) == b"<urlset/>"


# -- structured data ---------------------------------------------------------------------------


def _ld(obj) -> str:
    return json.dumps(obj)


def test_json_ld_syntax_vocabulary_and_feature_checks_are_separate() -> None:
    report = analyze_json_ld(
        [
            "{not json",
            _ld(
                {"@context": "https://schema.org", "@type": "Product", "name": "Widget"}
            ),
            _ld({"@type": "Organization", "name": "Acme"}),
            _ld(
                {
                    "@context": "https://schema.org",
                    "@graph": [
                        {
                            "@type": "BreadcrumbList",
                            "itemListElement": [{"@type": "ListItem", "name": "Home"}],
                        },
                        {"@type": "FAQPage", "mainEntity": []},
                        {
                            "@type": "WebSite",
                            "name": "Acme",
                            "url": "https://example.com",
                            "potentialAction": {"@type": "SearchAction", "target": "x"},
                        },
                    ],
                }
            ),
        ]
    )
    assert report.blocks == 4 and report.parse_errors == 1
    assert (
        report.issues[0].question == "syntax" and "line 1" in report.issues[0].message
    )
    by_type = {item.types[0]: item for item in report.items}
    product = [
        i
        for i in by_type["Product"].issues
        if i.question == "feature" and i.level == "warning"
    ]
    assert [i.property for i in product] == ["offers|review|aggregateRating"]
    assert any(
        i.question == "vocabulary" for i in by_type["Organization"].issues
    )  # no @context
    crumbs = by_type["BreadcrumbList"].issues
    assert any(i.property == "position" for i in crumbs)
    assert [i.question for i in by_type["FAQPage"].issues] == ["deprecated"]
    assert any(
        i.type == "SearchAction" and i.question == "deprecated"
        for i in by_type["WebSite"].issues
    )
    # Never a claim of eligibility: there is no "eligible" field anywhere in the report.
    assert "eligib" not in report.model_dump_json().lower()


def test_subtypes_use_parent_rules_and_complete_items_have_no_warnings() -> None:
    report = analyze_json_ld(
        [
            _ld(
                {
                    "@context": "https://schema.org",
                    "@type": "Restaurant",
                    "name": "Café",
                    "address": "1 Main St",
                    "telephone": "1",
                    "openingHoursSpecification": [],
                    "url": "https://x",
                }
            )
        ]
    )
    assert [i for i in report.items[0].issues if i.level == "warning"] == []


def test_headline_disagreement_with_visible_page_is_flagged() -> None:
    report = analyze_json_ld(
        [
            _ld(
                {
                    "@context": "https://schema.org",
                    "@type": "Article",
                    "headline": "Something else",
                }
            )
        ]
    )
    assert agreement_issues(report, ["Real title"], ["Real title"])
    assert not agreement_issues(
        analyze_json_ld(
            [
                _ld(
                    {
                        "@context": "https://schema.org",
                        "@type": "Article",
                        "headline": "Real title",
                    }
                )
            ]
        ),
        ["Real title"],
        [],
    )
