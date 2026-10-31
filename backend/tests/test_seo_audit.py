"""Technical SEO audit end to end on a scripted site: collection, rules,
recommendations, budgets, persistence and API (offline; SQLite)."""

import httpx
import pytest

from app.api.deps import get_seo_audit_service
from app.core.config import Settings
from app.database.repositories.seo_audit_repository import SeoAuditRepository
from app.main import create_app
from app.seo.technical.recommendations import build_recommendations
from app.seo.technical.rules import run_rules
from app.seo.technical.service import TechnicalSeoService
from tests.auth_support import AUTH_HEADERS
from tests.seo_support import FakeScraper, FakeSite, collector, page_html, policy

S = "https://example.com"
ORG_LD = '<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"Acme","url":"https://example.com/","logo":"https://example.com/l.png"}</script>'


def scripted_site() -> tuple[FakeSite, FakeScraper]:
    site, scraper = FakeSite(), FakeScraper()
    site.add(
        f"{S}/robots.txt",
        body="User-agent: *\nDisallow: /private/\nSitemap: https://example.com/sitemap.xml\n",
        content_type="text/plain",
    )
    site.add(
        f"{S}/sitemap.xml",
        content_type="application/xml",
        body=(
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(
                f"<url><loc>{S}{p}</loc></url>"
                for p in ("/", "/about", "/old", "/noindex-page", "/private/x")
            )
            + "</urlset>"
        ),
    )
    pages = {
        "/": page_html(
            "Acme",
            canonical=f"{S}/",
            head=ORG_LD,
            body=(
                '<a href="/about">About</a><a href="/missing">Missing</a><a href="/old">Old</a>'
                '<a href="/blog">Blog</a><a href="/private/x">Private</a><a href="/file.pdf">PDF</a>'
                '<a href="https://other.org/">Other site</a>'
            ),
        ),
        "/about": page_html("Acme", description=None, canonical=f"{S}/"),
        "/new": page_html("New page", canonical=f"{S}/old"),
        "/blog": page_html(
            "Blog",
            head='<meta name="robots" content="noindex">',
            body='<img src="/x.png"><a href="/gone">Gone</a>',
        ).replace("<h1>Blog</h1>", ""),
        "/noindex-page": page_html("Hidden", canonical=f"{S}/noindex-page"),
    }
    for path, html in pages.items():
        extra = {"x_robots_tag": "noindex"} if path == "/noindex-page" else {}
        site.add(f"{S}{path}", body=html, **extra)
        scraper.pages[f"{S}{path}"] = html
    site.redirect(f"{S}/old", f"{S}/new")
    site.add(f"{S}/missing", status=404)
    site.add(f"{S}/gone", status=410)
    return site, scraper


async def collect(site: FakeSite, scraper: FakeScraper, **kwargs):
    c, budget, client = collector(site, scraper, **kwargs)
    async with client:
        return await c.collect(f"{S}/"), budget


async def test_collection_respects_robots_and_only_scrapes_html_2xx_pages() -> None:
    site, scraper = scripted_site()
    result, budget = await collect(site, scraper)
    pages = {p.url: p for p in result.pages}

    assert pages[f"{S}/private/x"].fetch_status == "skipped_robots"
    assert not any("/private/" in url for _, url in site.requests)  # never requested
    assert (
        pages[f"{S}/missing"].status_code == 404
        and pages[f"{S}/missing"].fetch_source == "http_only"
    )
    assert f"{S}/missing" not in scraper.calls  # error pages cost no credit
    assert pages[f"{S}/old"].final_url == f"{S}/new" and f"{S}/new" in scraper.calls
    assert f"{S}/file.pdf" not in pages  # assets are not crawled as pages
    assert not any("other.org" in url for _, url in site.requests)
    assert budget.usage.firecrawl_scrapes == len(scraper.calls)
    assert budget.usage.direct_requests == len(site.requests)
    assert "sitemap" in pages[f"{S}/about"].discovered_via


async def test_indexability_never_claims_indexed_status() -> None:
    site, scraper = scripted_site()
    result, _ = await collect(site, scraper)
    pages = {p.url: p for p in result.pages}
    home = pages[f"{S}/"].indexability
    assert home.state == "indexable_candidate" and "Search Console" in home.note
    assert pages[f"{S}/missing"].indexability.reasons == ["http_404"]
    assert "noindex" in pages[f"{S}/blog"].indexability.reasons
    assert (
        "noindex" in pages[f"{S}/noindex-page"].indexability.reasons
    )  # from X-Robots-Tag
    assert "canonical_points_elsewhere" in pages[f"{S}/about"].indexability.reasons
    assert pages[f"{S}/private/x"].indexability.reasons == [
        "robots_txt_disallows_googlebot"
    ]
    assert all(p.indexability.state != "indexed" for p in result.pages)


async def test_rules_produce_evidence_backed_findings() -> None:
    site, scraper = scripted_site()
    result, _ = await collect(site, scraper)
    findings = {f.check_id: f for f in run_rules(result)}

    expected = {
        "robots.blocks_pages",
        "sitemap.lists_non_200",
        "sitemap.lists_noindex",
        "status.4xx",
        "title.duplicate",
        "description.missing",
        "canonical.points_elsewhere",
        "canonical.target_not_200",
        "indexability.noindex",
        "headings.h1_missing",
        "images.alt_missing",
        "links.broken_internal",
    }
    assert expected <= set(findings), expected - set(findings)
    for finding in findings.values():
        assert (
            finding.affected_urls
            and finding.affected_count >= len(finding.evidence) > 0
        )
        assert all(e.url and e.source for e in finding.evidence)
        assert finding.skill_refs
    assert set(findings["title.duplicate"].affected_urls) == {f"{S}/", f"{S}/about"}
    assert set(findings["status.4xx"].affected_urls) == {f"{S}/missing", f"{S}/gone"}
    broken = findings["links.broken_internal"]
    statuses = {e.url: e.observed["status"] for e in broken.evidence}
    assert statuses == {f"{S}/missing": 404, f"{S}/gone": 410}
    assert set(broken.affected_urls) == {
        f"{S}/",
        f"{S}/blog",
    }  # the pages that carry the links
    noindex_sources = {
        e.url: e.source for e in findings["indexability.noindex"].evidence
    }
    assert noindex_sources[f"{S}/noindex-page"] == "http_trace"  # header evidence
    assert noindex_sources[f"{S}/blog"] == "firecrawl_raw_html"
    html_evidence = [
        e
        for f in findings.values()
        for e in f.evidence
        if e.source == "firecrawl_raw_html"
    ]
    assert html_evidence and all(
        e.html_sha256 and len(e.html_sha256) == 64 for e in html_evidence
    )
    assert "schema.parse_error" not in findings  # valid Organization markup
    assert not any(
        "core web vitals" in f.title.lower() for f in findings.values()
    )  # never invented


async def test_recommendations_are_ranked_with_ordinal_labels_only() -> None:
    site, scraper = scripted_site()
    result, _ = await collect(site, scraper)
    findings = run_rules(result)
    recs = build_recommendations(findings, start_url=result.start_url, total_pages=6)
    assert [r.rank for r in recs] == list(range(1, len(recs) + 1))
    assert {r.finding_id for r in recs} == {f.id for f in findings}
    for rec in recs:
        assert {rec.impact, rec.confidence, rec.effort, rec.risk} <= {
            "high",
            "medium",
            "low",
        }
        assert rec.verification and rec.action
    tiers = [r.tier for r in recs]
    order = ["blocker", "cheap_certainty", "high_leverage", "maintenance", "deferred"]
    assert tiers == sorted(tiers, key=order.index)
    deferred = [r for r in recs if r.tier == "deferred"]
    assert all(
        r.check_id in {f.check_id for f in findings if f.severity == "info"}
        for r in deferred
    )


async def test_homepage_noindex_is_a_blocker() -> None:
    site, scraper = FakeSite(), FakeScraper()
    html = page_html(
        "Home", canonical=f"{S}/", head='<meta name="robots" content="noindex">'
    )
    site.add(f"{S}/", body=html)
    scraper.pages[f"{S}/"] = html
    result, _ = await collect(site, scraper)
    recs = build_recommendations(
        run_rules(result), start_url=result.start_url, total_pages=1
    )
    assert recs[0].check_id == "indexability.noindex" and recs[0].tier == "blocker"


async def test_scrape_budget_stops_collection_and_is_reported() -> None:
    site, scraper = FakeSite(), FakeScraper()
    links = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(8))
    for path in ["/"] + [f"/p{i}" for i in range(8)]:
        html = page_html(
            f"T {path}", canonical=f"{S}{path}", body=links if path == "/" else ""
        )
        site.add(f"{S}{path}", body=html)
        scraper.pages[f"{S}{path}"] = html
    result, budget = await collect(site, scraper, max_pages=9, max_scrapes=3)
    assert result.coverage.stopped_reason == "firecrawl_scrape_budget"
    assert len(scraper.calls) == 3 and budget.usage.firecrawl_scrapes == 3


async def test_direct_request_budget_and_deadline_stop_collection() -> None:
    site, scraper = scripted_site()
    small, _ = await collect(site, scraper, max_direct=3)
    assert small.coverage.stopped_reason == "direct_request_budget"
    assert len(site.requests) == 3
    site2, scraper2 = scripted_site()
    late, _ = await collect(site2, scraper2, deadline=-1)
    assert late.coverage.stopped_reason == "deadline" and site2.requests == []


async def test_provider_failure_is_recorded_per_page() -> None:
    site, scraper = scripted_site()
    scraper.failing.add(f"{S}/about")
    result, budget = await collect(site, scraper)
    about = next(p for p in result.pages if p.url == f"{S}/about")
    assert about.fetch_status == "scrape_failed" and about.signals is None
    assert budget.usage.firecrawl_failures == 1


async def test_link_check_cap_leaves_extra_targets_unverified() -> None:
    site, scraper = FakeSite(), FakeScraper()
    links = "".join(f'<a href="/x{i}">x</a>' for i in range(5))
    html = page_html("Home", canonical=f"{S}/", body=links)
    site.add(f"{S}/", body=html)
    scraper.pages[f"{S}/"] = html
    result, _ = await collect(site, scraper, max_pages=1, max_link_checks=2)
    outcomes = sorted(c.outcome for c in result.link_checks)
    assert (
        outcomes.count("broken") == 2 and outcomes.count("unverified") == 3
    )  # never assumed broken
    assert result.coverage.link_targets_checked == 2


# -- service + API (SQLite) ------------------------------------------------------------------


def _app(db_session, site: FakeSite, scraper: FakeScraper):
    app = create_app()
    service = TechnicalSeoService(
        repository=SeoAuditRepository(db_session),
        settings=Settings(),
        policy=policy(),
        scraper=scraper,
        client_factory=site.client,
    )
    app.dependency_overrides[get_seo_audit_service] = lambda: service
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=AUTH_HEADERS
    )


async def test_api_plan_approve_run_and_read(db_session) -> None:
    site, scraper = scripted_site()
    async with _app(db_session, site, scraper) as api:
        planned = await api.post(
            "/api/v1/seo/audits", json={"url": "https://example.com/", "max_pages": 10}
        )
        assert planned.status_code == 201, planned.text
        audit = planned.json()
        assert (
            audit["status"] == "planned"
            and audit["estimate"]["firecrawl_scrapes_max"] == 10
        )
        assert site.requests == [] and scraper.calls == []  # planning makes no request

        audit_id = audit["id"]
        assert (
            await api.post(
                f"/api/v1/seo/audits/{audit_id}/run",
                json={"approve": False, "max_firecrawl_scrapes": 5},
            )
        ).status_code == 422
        too_many = await api.post(
            f"/api/v1/seo/audits/{audit_id}/run",
            json={"approve": True, "max_firecrawl_scrapes": 11},
        )
        assert too_many.status_code == 422 and site.requests == []

        ran = await api.post(
            f"/api/v1/seo/audits/{audit_id}/run",
            json={"approve": True, "max_firecrawl_scrapes": 10},
        )
        assert ran.status_code == 200, ran.text
        done = ran.json()
        assert (
            done["status"] == "completed"
            and done["approval"]["max_firecrawl_scrapes"] == 10
        )
        assert done["usage"]["firecrawl_scrapes"] == len(scraper.calls)
        assert done["site_files"]["robots"]["applies"] == "rules"
        assert done["summary"]["findings"] > 0

        again = await api.post(
            f"/api/v1/seo/audits/{audit_id}/run",
            json={"approve": True, "max_firecrawl_scrapes": 1},
        )
        assert again.status_code == 409

        findings = (await api.get(f"/api/v1/seo/audits/{audit_id}/findings")).json()
        assert findings["total"] == done["summary"]["findings"]
        assert all(f["evidence"] and f["affected_urls"] for f in findings["items"])
        high = (
            await api.get(
                f"/api/v1/seo/audits/{audit_id}/findings", params={"severity": "high"}
            )
        ).json()
        assert all(f["severity"] == "high" for f in high["items"])
        recs = (await api.get(f"/api/v1/seo/audits/{audit_id}/recommendations")).json()
        assert [r["rank"] for r in recs["items"]] == list(range(1, recs["total"] + 1))

        pages = (await api.get(f"/api/v1/seo/audits/{audit_id}/pages")).json()
        home = next(p for p in pages["items"] if p["url"] == "https://example.com/")
        assert home["title"] == "Acme" and home["indexability"] == "indexable_candidate"
        detail = (
            await api.get(f"/api/v1/seo/audits/{audit_id}/pages/{home['id']}")
        ).json()
        assert (
            detail["signals"]["canonicals"] == ["https://example.com/"]
            and detail["html_sha256"]
        )
        assert detail["structured_data"]["items"][0]["types"] == ["Organization"]

        md = await api.get(
            f"/api/v1/seo/audits/{audit_id}/report", params={"format": "markdown"}
        )
        assert md.headers["content-type"].startswith("text/markdown")
        assert (
            "Core Web Vitals" in md.text
            and "Not measured" in md.text
            and "guarantees" in md.text
        )
        report = (await api.get(f"/api/v1/seo/audits/{audit_id}/report")).json()
        assert report["generated_from"].startswith("stored audit results")

        listed = (
            await api.get("/api/v1/seo/audits", params={"host": "example.com"})
        ).json()
        assert listed["total"] == 1


async def test_api_requires_firecrawl_before_anything_runs(db_session) -> None:
    site, scraper = scripted_site()
    scraper.configured = False
    async with _app(db_session, site, scraper) as api:
        audit_id = (
            await api.post("/api/v1/seo/audits", json={"url": "https://example.com/"})
        ).json()["id"]
        response = await api.post(
            f"/api/v1/seo/audits/{audit_id}/run",
            json={"approve": True, "max_firecrawl_scrapes": 1},
        )
        assert response.status_code == 503
        assert (await api.get(f"/api/v1/seo/audits/{audit_id}")).json()[
            "status"
        ] == "planned"
        assert site.requests == []


async def test_api_rejects_unsafe_or_oversized_requests(db_session) -> None:
    site, scraper = scripted_site()
    async with _app(db_session, site, scraper) as api:
        assert (
            await api.post("/api/v1/seo/audits", json={"url": "file:///etc/passwd"})
        ).status_code == 422
        assert (
            await api.post("/api/v1/seo/audits", json={"url": "http://localhost/"})
        ).status_code == 422
        assert (
            await api.post(
                "/api/v1/seo/audits",
                json={"url": "https://example.com/", "max_pages": 10_000},
            )
        ).status_code == 422
        assert (
            await api.post(
                "/api/v1/seo/audits", json={"url": "https://example.com/", "extra": 1}
            )
        ).status_code == 422
        assert (
            await api.post("/api/v1/seo/audits", json={"url": "https://db.internal/"})
        ).status_code == 422
        # Passes the name check at planning; DNS resolves to a private address at run time.
        private = (
            await api.post(
                "/api/v1/seo/audits", json={"url": "https://private.example.net/"}
            )
        ).json()
        ran = (
            await api.post(
                f"/api/v1/seo/audits/{private['id']}/run",
                json={"approve": True, "max_firecrawl_scrapes": 1},
            )
        ).json()
        assert ran["status"] == "failed" and "rejected" in ran["error_summary"]
        assert site.requests == [] and scraper.calls == []
        assert (
            await api.get("/api/v1/seo/audits/00000000-0000-0000-0000-000000000000")
        ).status_code == 404


async def test_seo_routes_require_the_api_token(db_session) -> None:
    site, scraper = scripted_site()
    async with _app(db_session, site, scraper) as api:
        response = await api.get("/api/v1/seo/audits", headers={"Authorization": ""})
    assert response.status_code == 401


@pytest.mark.parametrize("max_pages", [1, 25])
def test_estimate_is_an_upper_bound(max_pages: int) -> None:
    from app.seo.technical.models import AuditLimits
    from app.seo.technical.service import estimate_for

    limits = AuditLimits(
        max_pages=max_pages, max_link_checks=10, max_sitemaps=2, max_duration_seconds=60
    )
    estimate = estimate_for(limits, max_redirects=5)
    assert estimate.firecrawl_scrapes_max == max_pages
    assert estimate.direct_requests_max >= 15 + 7 * (max_pages + 10)
