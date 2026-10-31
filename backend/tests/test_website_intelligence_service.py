"""End-to-end tests of the website intelligence pipeline over fakes + SQLite."""

import asyncio
import logging
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.exceptions import ResearchException, ResourceNotFoundException
from app.database.models import ResearchSourceRecord
from app.research.robots import RobotsChecker
from app.research.text_utils import excerpt_in_text
from app.schemas.intelligence import (
    CrawlWebsiteRequest,
    DiscoverWebsitesRequest,
    EntityType,
    IntelligenceRequest,
    IntelligenceStatus,
    MapWebsiteRequest,
    OfficialStatus,
    ReportType,
)
from app.tools.errors import ProviderAuthenticationError, ProviderUnavailableError
from tests.fakes import make_policy
from tests.intel_support import (
    ABOUT_MD,
    HOME_MD,
    FakeCrawlProvider,
    FakeMapTool,
    QuerySearch,
    acme_links,
    acme_pages,
    acme_search,
    build_service,
    page,
    result,
)


def request(**kwargs) -> IntelligenceRequest:
    return IntelligenceRequest(**{"query": "Acme", "target_names": ["Acme"], **kwargs})


def acme_service(session, **overrides):
    return build_service(
        session,
        search=overrides.pop("search", acme_search()),
        mapper=overrides.pop("mapper", FakeMapTool(acme_links())),
        crawl=overrides.pop("crawl", FakeCrawlProvider(acme_pages())),
        **overrides,
    )


async def test_full_pipeline_produces_persisted_evidence_backed_results(
    db_session,
) -> None:
    service, fakes = acme_service(db_session)

    response = await service.run(request())

    assert response.status == IntelligenceStatus.COMPLETED
    assert (
        response.websites == 1
        and response.crawled_pages == 4
        and response.failed_pages == 0
    )
    assert response.documents == 4 and response.entities > 5 and response.claims > 10
    assert response.report_available and response.errors == []
    assert [s.stage for s in response.stages] == [
        "discovery", "mapping", "crawling", "extraction", "validation", "persistence", "reporting",
    ]  # fmt: skip
    # the unsafe, off-site, login and image URLs never reached the crawler
    submitted = {u for batch in fakes["crawl"].batch_calls for u in batch}
    assert submitted == {
        "https://acme.com/",
        "https://acme.com/about",
        "https://acme.com/pricing",
        "https://acme.com/product/flow",
    }

    websites = await service.get_websites(response.job_id, 50, 0)
    assert (
        websites.total == 1
        and websites.items[0].candidate.official_status == OfficialStatus.VERIFIED
    )
    entities = await service.get_entities(response.job_id, EntityType.COMPANY, 50, 0)
    assert entities.items[0].attributes["headquarters"] == "Bengaluru, India"
    prices = await service.get_claims(response.job_id, None, "price", 50, 0)
    assert prices.total >= 2 and all(
        c.verification_status.value in ("supported", "partially_supported")
        for c in prices.items
    )

    report = await service.get_report(response.job_id)
    assert report.report_type == ReportType.COMPANY_PROFILE
    assert report.data.companies[0].pricing_status == "found"
    assert "Bengaluru, India" in report.markdown


async def test_every_stored_excerpt_really_occurs_in_its_stored_source(
    db_session,
) -> None:
    service, _ = acme_service(db_session)
    response = await service.run(request())

    claims = (await service.get_claims(response.job_id, None, None, 200, 0)).items
    rows = (await db_session.execute(select(ResearchSourceRecord))).scalars().all()
    content = {r.normalized_url: r.extracted_markdown for r in rows}

    assert claims
    for claim in claims:
        assert claim.verification_status.value != "unsupported", claim
        assert excerpt_in_text(claim.evidence_excerpt, content[claim.source_url]), claim


async def test_status_progression_is_monotonic(db_session) -> None:
    service, _ = acme_service(db_session)
    seen: list[str] = []
    original = service._repo.update_job

    async def spy(job_id, status, **kwargs):
        seen.append(status.value)
        return await original(job_id, status, **kwargs)

    service._repo.update_job = spy  # type: ignore[method-assign]

    await service.run(request())

    order = [
        "discovering",
        "mapping",
        "crawling",
        "extracting",
        "validating",
        "reporting",
        "completed",
    ]
    assert [s for s in order if s in seen] == order
    assert seen == sorted(seen, key=order.index)


async def test_homepage_branding_upgrades_an_unverified_site(db_session) -> None:
    search = QuerySearch(
        {"Acme": [result(1, "https://acme.com/", "Welcome", "Home page")]}
    )
    service, _ = acme_service(db_session, search=search)

    response = await service.run(request())

    site = (await service.get_websites(response.job_id, 10, 0)).items[0].candidate
    assert site.official_status == OfficialStatus.VERIFIED
    assert any("homepage branding" in r for r in site.selection_reasons)


async def test_one_failing_website_does_not_stop_the_others(db_session) -> None:
    search = QuerySearch(
        {
            "Acme": [
                result(
                    1, "https://acme.com/", "Acme Automation - Official Site", "Acme"
                )
            ],
            "Globex": [
                result(1, "https://globex.com/", "Globex - Official Site", "Globex")
            ],
        }
    )

    class SelectiveMap(FakeMapTool):
        async def map_site(self, url, **kwargs):
            if "globex" in url:
                raise ProviderUnavailableError("Firecrawl", "down")
            return await super().map_site(url, **kwargs)

    service, _ = acme_service(
        db_session, search=search, mapper=SelectiveMap(acme_links())
    )

    response = await service.run(request(target_names=["Acme", "Globex"]))

    assert response.status == IntelligenceStatus.PARTIALLY_COMPLETED
    assert response.websites == 2 and response.crawled_pages == 4
    assert any("globex.com" in e for e in response.errors)
    assert (
        await service.get_entities(response.job_id, EntityType.COMPANY, 50, 0)
    ).total >= 1
    assert response.report_available


async def test_all_websites_failing_is_failed_with_a_reason(db_session) -> None:
    service, _ = acme_service(
        db_session,
        mapper=FakeMapTool(error=ProviderUnavailableError("Firecrawl", "down")),
    )

    response = await service.run(request())

    assert response.status == IntelligenceStatus.FAILED and response.crawled_pages == 0
    job = await service._repo.get_job(response.job_id)
    assert (
        job.status == "failed"
        and job.error_category == "intelligence_failed"
        and job.completed_at
    )


async def test_crawl_provider_credential_failure_is_reported_not_swallowed(
    db_session,
) -> None:
    crawl = FakeCrawlProvider(
        batch_error=ProviderAuthenticationError("Firecrawl", "bad key")
    )
    service, _ = acme_service(db_session, crawl=crawl)

    response = await service.run(request())

    assert response.status == IntelligenceStatus.FAILED
    assert any(
        "permanently" in e or "no page could be crawled" in e for e in response.errors
    )


async def test_discovery_provider_failure_marks_the_job_failed(db_session) -> None:
    service, _ = acme_service(
        db_session,
        search=QuerySearch(error=ProviderAuthenticationError("SerpApi", "bad key")),
    )

    response = await service.run(request())

    assert response.status == IntelligenceStatus.FAILED
    assert (
        response.errors == ["SerpApi: credentials rejected or access denied"]
        or "SerpApi" in response.errors[0]
    )
    assert (await service._repo.get_job(response.job_id)).status == "failed"


async def test_no_websites_found_is_failed(db_session) -> None:
    service, _ = acme_service(db_session, search=QuerySearch({}))

    response = await service.run(request())

    assert response.status == IntelligenceStatus.FAILED and response.websites == 0
    assert response.stages[0].status == "failed"


async def test_explicit_urls_skip_discovery_and_stay_unverified(db_session) -> None:
    search = QuerySearch()
    service, _ = acme_service(db_session, search=search)

    response = await service.run(
        request(
            website_urls=[
                "https://acme.com/some/page",
                "http://127.0.0.1/admin",
                "file:///etc/passwd",
            ]
        )
    )

    assert search.calls == []  # discovery was skipped
    site = (await service.get_websites(response.job_id, 10, 0)).items[0].candidate
    assert (
        site.discovery_source == "user_supplied"
        and site.homepage_url == "https://acme.com/"
    )
    assert site.official_status in (OfficialStatus.UNVERIFIED, OfficialStatus.VERIFIED)
    assert any(
        "URL rejected" in n for n in response.notes
    )  # rejected URLs are reported


async def test_total_page_budget_is_split_across_sites(db_session) -> None:
    search = QuerySearch(
        {
            "Acme": [result(1, "https://acme.com/", "Acme - Official", "Acme")],
            "Globex": [result(1, "https://globex.com/", "Globex - Official", "Globex")],
        }
    )
    crawl = FakeCrawlProvider()
    mapper = FakeMapTool(
        {
            "*": [
                "https://acme.com/pricing",
                "https://globex.com/pricing",
                "https://acme.com/a",
                "https://globex.com/a",
            ]
        }
    )
    service, _ = acme_service(
        db_session, search=search, mapper=mapper, crawl=crawl,
        settings=Settings(website_research_respect_robots=False, website_research_max_total_pages=2),
    )  # fmt: skip

    await service.run(request(target_names=["Acme", "Globex"]))

    assert sum(len(b) for b in crawl.batch_calls) == 2  # 1 page per site


async def test_reports_can_be_disabled(db_session) -> None:
    service, _ = acme_service(
        db_session,
        settings=Settings(
            website_research_respect_robots=False, website_research_enable_reports=False
        ),
    )

    response = await service.run(request())

    assert response.report_available is False
    with pytest.raises(ResourceNotFoundException):
        await service.get_report(response.job_id)


async def test_robots_txt_exclusions_are_respected_and_reported(db_session) -> None:
    async def fetcher(url: str) -> str | None:
        return "User-agent: *\nDisallow: /pricing\n"

    robots = RobotsChecker(make_policy(), fetcher)
    service, fakes = acme_service(db_session, robots=robots)

    response = await service.run(request())

    submitted = {u for batch in fakes["crawl"].batch_calls for u in batch}
    assert "https://acme.com/pricing" not in submitted
    report = await service.get_report(response.job_id)
    assert report.data.companies[0].pricing_status.startswith("not publicly found")


async def test_conflicting_facts_across_pages_are_detected_end_to_end(
    db_session,
) -> None:
    pages = acme_pages()
    pages["https://acme.com/about"] = page(
        "https://acme.com/about",
        "# About Acme\n\nOur head office is in Mumbai, India.\n",
        "About",
    )
    service, _ = acme_service(db_session, crawl=FakeCrawlProvider(pages))

    response = await service.run(request())

    claims = (
        await service.get_claims(response.job_id, "conflicting", "headquarters", 50, 0)
    ).items
    assert {c.object_value for c in claims} == {"Bengaluru, India", "Mumbai, India"}
    report = await service.get_report(response.job_id)
    assert any("headquarters" in c for c in report.data.conflicting_information)
    assert response.coverage.conflicting >= 2


async def test_pricing_is_never_invented(db_session) -> None:
    pages = acme_pages()
    pages["https://acme.com/pricing"] = page(
        "https://acme.com/pricing",
        "# Pricing\n\nTalk to us about flexible plans.\n",
        "Pricing",
    )
    service, _ = acme_service(db_session, crawl=FakeCrawlProvider(pages))

    response = await service.run(request())

    assert (await service.get_claims(response.job_id, None, "price", 50, 0)).total == 0
    entry = (await service.get_report(response.job_id)).data.companies[0]
    assert (
        entry.pricing == []
        and entry.pricing_status == "not publicly found (checked 1 pricing page(s))"
    )
    assert response.coverage.pricing_found is False


async def test_deadline_keeps_completed_sites_and_marks_the_job(db_session) -> None:
    search = QuerySearch(
        {
            "Acme": [result(1, "https://acme.com/", "Acme - Official", "Acme")],
            "Globex": [result(1, "https://globex.com/", "Globex - Official", "Globex")],
        }
    )

    class SlowForGlobex(FakeCrawlProvider):
        async def batch_scrape(self, urls):
            if any("globex" in u for u in urls):
                await asyncio.sleep(5)
            return await super().batch_scrape(urls)

    service, _ = acme_service(
        db_session, search=search, mapper=FakeMapTool(acme_links()), crawl=SlowForGlobex(acme_pages()),
        settings=Settings(website_research_respect_robots=False, website_research_timeout_seconds=0.5),
    )  # fmt: skip

    response = await service.run(request(target_names=["Acme", "Globex"]))

    assert response.status == IntelligenceStatus.PARTIALLY_COMPLETED
    assert any("deadline" in e for e in response.errors)
    assert (
        await service.get_entities(response.job_id, EntityType.COMPANY, 50, 0)
    ).total >= 1  # acme kept


async def test_cancellation_is_persisted_and_propagated(db_session) -> None:
    class Hang(FakeCrawlProvider):
        async def batch_scrape(self, urls):
            await asyncio.sleep(10)

    service, _ = acme_service(db_session, crawl=Hang(acme_pages()))

    task = asyncio.create_task(service.run(request()))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    job = (
        (
            await db_session.execute(
                select(
                    __import__("app.database.models", fromlist=["x"]).ResearchJobRecord
                )
            )
        )
        .scalars()
        .one()
    )
    assert (
        job.status in ("failed", "partially_completed") and job.completed_at is not None
    )


async def test_progress_endpoint_data(db_session) -> None:
    service, _ = acme_service(db_session)
    response = await service.run(request())

    progress = await service.get_progress(response.job_id)

    assert progress.status == IntelligenceStatus.COMPLETED and progress.websites == 1
    assert progress.crawled_pages == 4 and progress.report_available
    assert {s.stage for s in progress.stages} >= {"discovery", "crawling", "reporting"}
    with pytest.raises(ResourceNotFoundException):
        await service.get_progress(uuid4())


# -- staged workflow ---------------------------------------------------------------------


async def test_staged_discover_map_crawl_flow(db_session) -> None:
    service, fakes = acme_service(db_session)

    discovered = await service.discover(
        DiscoverWebsitesRequest(query="Acme", target_names=["Acme"])
    )
    (site,) = discovered.websites
    assert discovered.status == IntelligenceStatus.COMPLETED
    assert site.candidate.official_status == OfficialStatus.VERIFIED

    mapped = await service.map_website(site.website_id, MapWebsiteRequest())
    assert mapped.eligible_pages == 4 and mapped.discovered_urls == 7
    assert {p.page_type.value for p in mapped.pages} >= {
        "homepage",
        "pricing",
        "product",
        "about",
    }

    crawled = await service.crawl_website(
        site.website_id, CrawlWebsiteRequest(max_pages=3)
    )
    assert crawled.crawl.successful_pages == 3 and crawled.documents == 3
    assert (
        crawled.entities > 0
        and crawled.claims > 0
        and crawled.coverage.total_claims == crawled.claims
    )
    assert len(fakes["crawl"].batch_calls[0]) == 3

    report = await service.get_report(discovered.job_id)  # regenerated from stored data
    assert report.data.companies[0].name == "Acme"


async def test_crawl_requires_mapping_and_known_website(db_session) -> None:
    service, _ = acme_service(db_session)
    discovered = await service.discover(
        DiscoverWebsitesRequest(query="Acme", target_names=["Acme"])
    )

    with pytest.raises(ResearchException, match="map it first"):
        await service.crawl_website(
            discovered.websites[0].website_id, CrawlWebsiteRequest()
        )
    with pytest.raises(ResourceNotFoundException):
        await service.map_website(uuid4(), MapWebsiteRequest())
    with pytest.raises(ResourceNotFoundException):
        await service.get_websites(uuid4(), 10, 0)


async def test_staged_crawl_can_skip_extraction(db_session) -> None:
    service, _ = acme_service(db_session)
    discovered = await service.discover(
        DiscoverWebsitesRequest(query="Acme", target_names=["Acme"])
    )
    site_id = discovered.websites[0].website_id
    await service.map_website(site_id, MapWebsiteRequest())

    crawled = await service.crawl_website(site_id, CrawlWebsiteRequest(extract=False))

    assert crawled.documents == 4 and crawled.entities == 0 and crawled.claims == 0


async def test_logs_do_not_contain_page_content_or_secrets(db_session, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    service, _ = acme_service(db_session)

    await service.run(request())

    # Only AURORA's own loggers: the DB driver's DEBUG SQL logging is out of scope here.
    text = "\n".join(
        r.getMessage() for r in caplog.records if r.name.startswith("app.")
    )
    assert (
        "research_job_id=" in text
        and "website_mapped" in text
        and "website_crawled" in text
    )
    assert (
        "Bengaluru" not in text
        and HOME_MD[:30] not in text
        and ABOUT_MD[:20] not in text
    )
