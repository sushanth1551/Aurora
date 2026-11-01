"""Keyword research end to end on a scripted SERP provider (offline; SQLite)."""

import httpx
import pytest

from app.api.deps import get_keyword_service
from app.core.config import Settings
from app.database.models import SeoAuditPageRecord, SeoAuditRecord
from app.database.repositories.keyword_repository import KeywordRepository
from app.database.repositories.seo_audit_repository import SeoAuditRepository
from app.main import create_app
from app.schemas.research import utc_now
from app.seo.keywords.service import KeywordResearchService
from tests.auth_support import AUTH_HEADERS
from tests.keyword_support import EMAIL_RESULTS, FakeSerp, organic, payload
from tests.seo_support import policy

SITE = "https://www.example.com/"


def serp_pages() -> dict[str, dict]:
    return {
        "email marketing": payload(
            EMAIL_RESULTS,
            related_questions=[
                {
                    "question": "How does email automation reduce churn?",
                    "link": "https://rival-one.com/q",
                }
            ],
            related_searches=[{"query": "email marketing automation"}],
            ai_overview={
                "text_blocks": [{"snippet": "Overview."}],
                "references": [{"link": "https://rival-one.com/x"}],
            },
        ),
        "email marketing automation": payload(
            organic(
                (
                    "rival-one.com",
                    "Email Marketing Automation Guide",
                    "Automation workflows.",
                ),
                (
                    "rival-two.com",
                    "Email Marketing Automation Tips",
                    "Email marketing automation.",
                ),
                ("rival-three.com", "Drip Campaign Examples", "Drip campaigns."),
                ("a.com", "A", "a"),
                ("b.com", "B", "b"),
            )
        ),
        "How does email automation reduce churn?": payload(
            organic(
                ("rival-one.com", "Reduce churn with automation", "x"),
                ("a.com", "A", "a"),
            )
        ),
        "welcome series examples": payload(
            organic(("example.com", "Welcome Series Examples", "x"))
        ),
    }


async def seed_audit(
    session, host="example.com", titles=("Acme Email Platform",)
) -> str:
    audit = SeoAuditRecord(
        id=__import__("uuid").uuid4(),
        site_url=f"https://{host}/",
        host=host,
        status="completed",
        limits={},
        estimate={},
        approval=None,
        usage={},
        coverage={},
        site_files={},
        summary={},
        created_at=utc_now(),
    )
    session.add(audit)
    await session.flush()
    for i, title in enumerate(titles):
        session.add(
            SeoAuditPageRecord(
                id=__import__("uuid").uuid4(),
                audit_id=audit.id,
                url=f"https://{host}/p{i}",
                status_code=200,
                status_source="http_trace",
                depth=0,
                discovered_via=[],
                redirect_chain=[],
                headers={},
                fetch_source="firecrawl_raw_html",
                fetch_status="ok",
                html_truncated=False,
                indexability={},
                signals={
                    "titles": [title],
                    "headings": [
                        {"level": 1, "text": title},
                        {"level": 2, "text": "Welcome Series Examples"},
                    ],
                },
            )
        )
    await session.commit()
    return str(audit.id)


def make_service(
    db_session, serp: FakeSerp, sleeps: list[float] | None = None, **settings
):
    async def sleep(delay: float) -> None:
        if sleeps is not None:
            sleeps.append(delay)

    return KeywordResearchService(
        repository=KeywordRepository(db_session),
        audit_repository=SeoAuditRepository(db_session),
        settings=Settings(seo_serp_min_interval_seconds=1.0, **settings),
        policy=policy(),
        serp=serp,
        sleep=sleep,
    )


# -- planning -------------------------------------------------------------------------------


async def test_plan_makes_no_search_and_takes_topics_from_the_stored_audit(
    db_session,
) -> None:
    await seed_audit(db_session)
    serp = FakeSerp()
    service = make_service(db_session, serp)
    run = await service.plan(
        SITE, seeds=["Email Marketing", "email  marketing"], country="us", language="en"
    )
    assert serp.calls == [] and run.status == "planned"
    assert run.params["seeds"] == [
        "Email Marketing"
    ]  # deduplicated after normalization
    assert "Acme Email Platform" in run.params["site_topics"] and run.params["audit_id"]
    assert run.params["planned_queries"][0] == "Email Marketing"
    assert run.estimate["serpapi_searches_max"] == 10
    assert (
        run.estimate["estimated_cost"] is None
        and "not configured" in run.estimate["cost_note"]
    )


async def test_plan_requires_seeds_or_a_stored_audit(db_session) -> None:
    from app.core.exceptions import ResearchException

    with pytest.raises(ResearchException, match="seed keyword"):
        await make_service(db_session, FakeSerp()).plan(SITE, seeds=[])


async def test_plan_estimates_cost_only_when_a_price_is_configured(db_session) -> None:
    run = await make_service(
        db_session, FakeSerp(), seo_serpapi_cost_per_search=0.01
    ).plan(SITE, seeds=["email marketing"], max_queries=5)
    assert (
        run.estimate["estimated_cost"] == 0.05
        and "Upper bound" in run.estimate["cost_note"]
    )


async def test_plan_rejects_unsafe_urls_bad_devices_and_oversized_limits(
    db_session,
) -> None:
    from app.core.exceptions import ResearchException

    service = make_service(db_session, FakeSerp())
    for kwargs in (
        {"site_url": "http://localhost/"},
        {"site_url": SITE, "device": "watch"},
        {"site_url": SITE, "max_queries": 11},
        {"site_url": SITE, "seeds": [f"k{i} x" for i in range(11)]},
    ):
        with pytest.raises(ResearchException):
            await service.plan(kwargs.pop("site_url"), **{"seeds": ["email"], **kwargs})


# -- running ----------------------------------------------------------------------------------


async def test_run_discovers_keywords_attaches_serp_evidence_and_stays_within_budget(
    db_session,
) -> None:
    await seed_audit(db_session)
    serp, sleeps = FakeSerp(pages=serp_pages()), []
    service = make_service(db_session, serp, sleeps)
    run = await service.plan(
        SITE, seeds=["email marketing"], country="us", language="en", max_queries=5
    )
    done = await service.run(run.id, max_searches=5)

    assert (
        len(serp.calls) == 5 and done.usage["serpapi_searches"] == 5
    )  # never above the approved ceiling
    # Order: seed, then site topics, then queries discovered on fetched SERPs.
    assert serp.calls[:3] == [
        "email marketing",
        "Acme Email Platform",
        "Welcome Series Examples",
    ]
    assert set(serp.calls[3:]) == {
        "How does email automation reduce churn?",
        "email marketing automation",
    }
    assert sleeps == [1.0] * 4  # spaced between searches, not before the first
    # Every queued query was searched within the ceiling, so nothing stopped the run.
    assert done.status == "completed" and done.usage["stopped_reason"] is None

    api_keywords = {
        c.normalized: c for c in (await service.repository.candidates(run.id))[0]
    }
    assert (
        api_keywords["email marketing"].source == "seed"
        and api_keywords["email marketing"].searched
    )
    assert api_keywords["email marketing automation"].source == "serp_related"
    assert api_keywords["how does email automation reduce churn"].source == "serp_paa"
    assert (
        api_keywords["email marketing"].intent is None
        or api_keywords["email marketing"].intent_basis
    )
    for candidate in api_keywords.values():
        assert candidate.metrics["search_volume"] == {
            "value": None,
            "status": "not_available",
            "source": None,
            "observed_at": None,
        }

    opportunities = await service.repository.opportunities(run.id)
    assert opportunities and [o.rank for o in opportunities] == list(
        range(1, len(opportunities) + 1)
    )
    first = next(o for o in opportunities if o.normalized == "email marketing")
    assert (
        first.serp["snapshot_id"]
        and first.serp["organic_count"] == 5
        and first.serp["paa_count"] == 1
    )
    assert first.serp["ai_overview"] == "present" and first.serp["site_position"] == 3
    snapshot = await service.repository.get_snapshot(
        __import__("uuid").UUID(first.serp["snapshot_id"])
    )
    assert snapshot and snapshot.features["related_searches"] == [
        "email marketing automation"
    ]
    assert done.competitors and done.competitors[0]["domain"] == "rival-one.com"
    assert done.gap_status == "ok" and any(
        g["term"] == "marketing automation" for g in done.gaps
    )


async def test_unsearched_candidates_never_become_opportunities(db_session) -> None:
    serp = FakeSerp(pages=serp_pages())
    service = make_service(db_session, serp)
    run = await service.plan(SITE, seeds=["email marketing"], max_queries=1)
    done = await service.run(run.id, max_searches=1)
    assert len(serp.calls) == 1 and done.summary["unsearched_candidates"] >= 1
    opportunities = await service.repository.opportunities(run.id)
    assert {o.normalized for o in opportunities} == {"email marketing"}
    unsearched, _ = await service.repository.candidates(run.id, searched=False)
    assert unsearched and all(c.cluster is None for c in unsearched)


async def test_gaps_are_not_claimed_without_a_stored_audit(db_session) -> None:
    service = make_service(db_session, FakeSerp(pages=serp_pages()))
    run = await service.plan(SITE, seeds=["email marketing"], max_queries=2)
    done = await service.run(run.id, max_searches=2)
    assert done.gap_status == "site_content_unavailable" and done.gaps == []


async def test_cached_snapshots_are_reused_and_cost_nothing(db_session) -> None:
    serp = FakeSerp(pages=serp_pages())
    service = make_service(db_session, serp)
    first = await service.plan(
        SITE, seeds=["plain topic"], country="us", language="en", max_queries=1
    )
    await service.run(first.id, max_searches=1)
    assert serp.calls == ["plain topic"]
    second = await service.plan(
        SITE, seeds=["plain topic"], country="us", language="en", max_queries=1
    )
    assert second.estimate["cached_snapshots_possible"] == 1
    done = await service.run(second.id, max_searches=1)
    assert serp.calls == ["plain topic"]  # no new search
    assert done.usage["cache_hits"] == 1 and done.usage["serpapi_searches"] == 0
    opportunity = (await service.repository.opportunities(second.id))[0]
    assert opportunity.serp["cache_hit"] is True
    # Different parameters are a different cache key.
    third = await service.plan(
        SITE, seeds=["plain topic"], country="gb", language="en", max_queries=1
    )
    assert third.estimate["cached_snapshots_possible"] == 0


async def test_cache_can_be_disabled(db_session) -> None:
    serp = FakeSerp(pages=serp_pages())
    service = make_service(db_session, serp, seo_serp_cache_ttl_hours=0)
    for _ in range(2):
        run = await service.plan(SITE, seeds=["plain topic"], max_queries=1)
        await service.run(run.id, max_searches=1)
    assert serp.calls == ["plain topic"] * 2


async def test_provider_failures_are_counted_and_stop_after_three_in_a_row(
    db_session,
) -> None:
    pages = serp_pages()
    serp = FakeSerp(pages=pages, failing={"a one", "b two", "c three"})
    service = make_service(db_session, serp)
    run = await service.plan(
        SITE, seeds=["a one", "b two", "c three", "email marketing"], max_queries=10
    )
    done = await service.run(run.id, max_searches=10)
    assert (
        done.usage["failures"] == 3
        and done.usage["stopped_reason"] == "provider_failures"
    )
    assert done.status == "partially_completed" and "email marketing" not in serp.calls


async def test_run_needs_approval_within_the_estimate_and_cannot_repeat(
    db_session,
) -> None:
    from app.core.exceptions import (
        ConfigurationException,
        ConflictException,
        ResearchException,
    )

    serp = FakeSerp(pages=serp_pages())
    service = make_service(db_session, serp)
    run = await service.plan(SITE, seeds=["email marketing"], max_queries=2)
    with pytest.raises(ResearchException):
        await service.run(run.id, max_searches=3)
    assert serp.calls == []
    unconfigured = FakeSerp(configured=False)
    with pytest.raises(ConfigurationException):
        await make_service(db_session, unconfigured).run(run.id, max_searches=1)
    assert (await service.get(run.id)).status == "planned" and unconfigured.calls == []
    await service.run(run.id, max_searches=1)
    with pytest.raises(ConflictException):
        await service.run(run.id, max_searches=1)


# -- API -----------------------------------------------------------------------------------


def api(db_session, serp: FakeSerp) -> httpx.AsyncClient:
    app = create_app()
    service = make_service(db_session, serp)
    app.dependency_overrides[get_keyword_service] = lambda: service
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=AUTH_HEADERS
    )


async def test_api_journey_url_to_selected_opportunity(db_session) -> None:
    await seed_audit(db_session)
    serp = FakeSerp(pages=serp_pages())
    async with api(db_session, serp) as client:
        planned = await client.post(
            "/api/v1/seo/keywords/runs",
            json={
                "site_url": "https://www.example.com/",
                "seeds": ["email marketing"],
                "country": "US",
                "language": "en",
                "max_queries": 4,
            },
        )
        assert planned.status_code == 201, planned.text
        run = planned.json()
        assert (
            run["status"] == "planned"
            and serp.calls == []
            and run["params"]["country"] == "us"
        )

        assert (
            await client.post(
                f"/api/v1/seo/keywords/runs/{run['id']}/run",
                json={"approve": False, "max_searches": 2},
            )
        ).status_code == 422
        assert (
            await client.post(
                f"/api/v1/seo/keywords/runs/{run['id']}/run",
                json={"approve": True, "max_searches": 99},
            )
        ).status_code == 422
        assert serp.calls == []

        done = await client.post(
            f"/api/v1/seo/keywords/runs/{run['id']}/run",
            json={"approve": True, "max_searches": 4},
        )
        assert done.status_code == 200, done.text
        body = done.json()
        assert body["usage"]["serpapi_searches"] == len(serp.calls) <= 4
        assert body["summary"]["opportunities"] >= 1

        base = f"/api/v1/seo/keywords/runs/{run['id']}"
        ops = (await client.get(f"{base}/opportunities")).json()
        assert ops["total"] >= 1
        target = next(o for o in ops["items"] if o["keyword"] == "email marketing")
        assert (
            target["metrics"]["search_volume"]["status"] == "not_available"
            and target["serp"]["snapshot_id"]
        )
        assert target["selected"] is False

        serp_view = (
            await client.get(
                f"/api/v1/seo/keywords/serp/{target['serp']['snapshot_id']}"
            )
        ).json()
        assert (
            serp_view["query"] == "email marketing" and len(serp_view["organic"]) == 5
        )
        assert serp_view["features"]["people_also_ask"][0]["question"].startswith(
            "How does email"
        )

        competitors = (await client.get(f"{base}/competitors")).json()
        domains = [c["domain"] for c in competitors["items"]]
        assert "example.com" not in domains and "rival-one.com" in domains
        comparison = (
            await client.get(
                f"{base}/competitors/compare",
                params=[("domains", "rival-one.com"), ("domains", "rival-two.com")],
            )
        ).json()
        assert (
            "email marketing" in comparison["shared_keywords"]
            and "observed positions" in comparison["basis"]
        )
        assert (
            await client.get(
                f"{base}/competitors/compare", params=[("domains", "rival-one.com")]
            )
        ).status_code == 422

        gaps = (await client.get(f"{base}/gaps")).json()
        assert (
            gaps["status"] == "ok"
            and gaps["items"]
            and gaps["items"][0]["evidence"][0]["excerpt"]
        )
        candidates = (
            await client.get(f"{base}/candidates", params={"searched": "true"})
        ).json()
        assert candidates["total"] == len(serp.calls)

        chosen = await client.post(
            f"/api/v1/seo/keywords/opportunities/{target['id']}/selection",
            json={"selected": True},
        )
        assert (
            chosen.status_code == 200
            and chosen.json()["selected"] is True
            and chosen.json()["selected_at"]
        )
        selected = (
            await client.get(
                "/api/v1/seo/keywords/selected", params={"host": "example.com"}
            )
        ).json()
        assert [s["keyword"] for s in selected["items"]] == ["email marketing"]
        assert selected["items"][0]["serp_snapshot_id"] == target["serp"]["snapshot_id"]
        filtered = (
            await client.get(f"{base}/opportunities", params={"selected": "true"})
        ).json()
        assert filtered["total"] == 1
        await client.post(
            f"/api/v1/seo/keywords/opportunities/{target['id']}/selection",
            json={"selected": False},
        )
        assert (await client.get("/api/v1/seo/keywords/selected")).json()["items"] == []


async def test_api_errors_and_auth(db_session) -> None:
    async with api(db_session, FakeSerp(configured=False)) as client:
        missing = "00000000-0000-0000-0000-000000000000"
        assert (
            await client.get(f"/api/v1/seo/keywords/runs/{missing}")
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/seo/keywords/serp/{missing}")
        ).status_code == 404
        assert (
            await client.post(
                f"/api/v1/seo/keywords/opportunities/{missing}/selection",
                json={"selected": True},
            )
        ).status_code == 404
        for bad in (
            {"site_url": "https://example.com/", "country": "usa"},
            {"site_url": "https://example.com/", "x": 1},
            {"site_url": "https://example.com/", "device": "tv"},
        ):
            assert (
                await client.post("/api/v1/seo/keywords/runs", json=bad)
            ).status_code == 422
        assert (
            await client.post(
                "/api/v1/seo/keywords/runs", json={"site_url": "https://example.com/"}
            )
        ).status_code == 422
        planned = (
            await client.post(
                "/api/v1/seo/keywords/runs",
                json={"site_url": "https://example.com/", "seeds": ["email"]},
            )
        ).json()
        unconfigured = await client.post(
            f"/api/v1/seo/keywords/runs/{planned['id']}/run",
            json={"approve": True, "max_searches": 1},
        )
        assert unconfigured.status_code == 503
        assert (
            await client.get("/api/v1/seo/keywords/runs", headers={"Authorization": ""})
        ).status_code == 401


async def test_url_alone_produces_opportunities_from_the_stored_audit_topics(
    db_session,
) -> None:
    """Acceptance: a website URL with no seeds yields opportunities with SERP evidence."""
    await seed_audit(
        db_session, titles=("Acme Email Platform", "Drip Campaign Examples")
    )
    pages = serp_pages()
    pages["Drip Campaign Examples"] = payload(
        organic(
            ("rival-one.com", "Drip Campaign Examples", "Examples."),
            ("rival-two.com", "Drip Examples", "More."),
            ("a.com", "A", "a"),
            ("b.com", "B", "b"),
        )
    )
    serp = FakeSerp(pages=pages)
    service = make_service(db_session, serp)
    run = await service.plan(SITE, seeds=[], max_queries=3)
    assert (
        run.params["seeds"] == []
        and "Drip Campaign Examples" in run.params["site_topics"]
    )
    done = await service.run(run.id, max_searches=3)
    opportunities = await service.repository.opportunities(run.id)
    assert done.usage["serpapi_searches"] == len(serp.calls) <= 3 and opportunities
    assert all(
        o.serp["snapshot_id"] and o.serp["organic_count"] >= 0 for o in opportunities
    )
    drip = next(o for o in opportunities if o.normalized == "drip campaign examples")
    # The site already has a page with this title and nothing new was observed, so it is not "write new".
    assert drip.action == "low_evidence" and drip.intent == "informational"
    assert drip.rationale[0].startswith("No gap term")
