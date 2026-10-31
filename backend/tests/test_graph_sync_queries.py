"""Graph sync (outbox, idempotency, retry, reconcile), queries, Cypher safety and API."""

from collections import Counter
from uuid import uuid4

import httpx
import pytest

from app.api.deps import get_graph_query_service, get_graph_sync_service
from app.core.config import Settings
from app.core.exceptions import ResourceNotFoundException
from app.database.repositories.graph_sync_repository import GraphSyncRepository
from app.database.session import get_session
from app.graph.graph_exceptions import (
    GraphNotConfiguredError,
    GraphQueryError,
    GraphSyncDisabledError,
    GraphUnavailableError,
    GraphValidationError,
)
from app.graph.graph_query_service import GraphQueryService
from app.graph.graph_repository import InMemoryGraphRepository, Neo4jGraphRepository
from app.graph.graph_schema import Label, Rel
from app.graph.normalization import company_id_for_domain
from app.main import create_app
from tests.analysis_support import seed_acme_job
from tests.auth_support import AUTH_HEADERS
from tests.graph_support import FlakyGraph, sync_service

ACME = company_id_for_domain("acme.com")


# -- sync ---------------------------------------------------------------------------------


async def test_sync_projects_job_with_provenance_and_is_idempotent(db_session) -> None:
    job = await seed_acme_job(db_session)
    graph = InMemoryGraphRepository()
    svc = sync_service(db_session, graph)
    run = await svc.sync_job(job)
    assert run.status == "completed" and run.counts.entities_created > 0
    assert run.counts.claim_nodes_created == run.counts.claims_linked > 0
    snapshot = (
        dict(Counter(n.label for n in graph.nodes.values())),
        len(graph.edges_by_key),
    )
    again = await svc.sync_job(job)
    assert (
        again.counts.entities_created == again.counts.relationships_created == 0
    )  # nothing duplicated
    assert (
        dict(Counter(n.label for n in graph.nodes.values())),
        len(graph.edges_by_key),
    ) == snapshot
    # every fact edge points back to stored claims and sources
    facts = [
        e
        for e in graph.edges_by_key.values()
        if e.type not in (Rel.SUPPORTS, Rel.MENTIONS)
    ]
    assert facts and all(
        e.properties["claim_ids"] and e.properties["source_ids"] for e in facts
    )
    for e in facts:
        for cid in e.properties["claim_ids"]:
            claim = graph.nodes[cid]
            assert claim.label is Label.CLAIM and claim.properties[
                "research_job_id"
            ] == str(job)
    repo = GraphSyncRepository(db_session)
    assert (await repo.get_event(job)).status == "succeeded"
    assert [r.status for r in await repo.runs_for_job(job)] == [
        "completed",
        "completed",
    ]


async def test_dry_run_writes_nothing_and_needs_no_graph(db_session) -> None:
    job = await seed_acme_job(db_session)
    svc = sync_service(db_session, graph=None)
    svc._graph = None
    run = await svc.sync_job(job, dry_run=True)
    assert run.status == "dry_run" and run.counts.entities_processed > 0
    assert await GraphSyncRepository(db_session).get_event(job) is None
    with pytest.raises(GraphNotConfiguredError):
        await svc.sync_job(job)


async def test_two_jobs_on_the_same_site_share_entities_and_keep_all_evidence(
    db_session,
) -> None:
    first, second = await seed_acme_job(db_session), await seed_acme_job(db_session)
    graph = InMemoryGraphRepository()
    svc = sync_service(db_session, graph)
    await svc.sync_job(first)
    run2 = await svc.sync_job(second)
    assert run2.counts.duplicates_detected > 0 and run2.counts.entities_created == 0
    companies = [n for n in graph.nodes.values() if n.label is Label.COMPANY]
    assert [c.id for c in companies] == [ACME]
    assert set(companies[0].properties["research_job_ids"]) == {str(first), str(second)}
    offers = next(e for e in graph.edges_by_key.values() if e.type is Rel.OFFERS)
    jobs = {
        graph.nodes[c].properties["research_job_id"]
        for c in offers.properties["claim_ids"]
    }
    assert jobs == {
        str(first),
        str(second),
    }  # supporting claims from both jobs preserved
    sources = [n for n in graph.nodes.values() if n.label is Label.SOURCE]
    assert all(
        len(s.properties["source_record_ids"]) == 2 for s in sources
    )  # one node per URL


async def test_outage_marks_failed_then_retry_succeeds(db_session) -> None:
    job = await seed_acme_job(db_session)
    settings = Settings(graph_sync_max_retries=1)
    flaky = FlakyGraph(failures=10)
    svc = sync_service(db_session, flaky, settings)
    with pytest.raises(GraphUnavailableError):
        await svc.sync_job(job)
    repo = GraphSyncRepository(db_session)
    event = await repo.get_event(job)
    assert (
        event.status == "failed"
        and event.next_retry_at is not None
        and event.attempt_count == 1
    )
    assert flaky.attempts == 2  # first try + 1 bounded retry
    runs = await repo.runs_for_job(job)
    assert runs[0].status == "failed" and runs[0].counts["retries"] == 1
    # the event is due later; force it due and recover
    event.next_retry_at = None
    await db_session.commit()
    flaky.failures = 0
    results = await svc.process_due(include_pending=False)
    assert [r.status for r in results] == ["completed"]
    assert (await repo.get_event(job)).status == "succeeded"


async def test_permanent_failures_go_dead_after_max_attempts(db_session) -> None:
    job = await seed_acme_job(db_session)
    svc = sync_service(
        db_session,
        FlakyGraph(failures=99, error=GraphQueryError()),
        Settings(graph_sync_max_retries=1),
    )
    for _ in range(2):
        with pytest.raises(GraphQueryError):
            await svc.sync_job(job)
    event = await GraphSyncRepository(db_session).get_event(job)
    assert event.status == "dead" and event.attempt_count == 2
    assert await svc.process_due() == []  # dead events are not retried automatically


async def test_reconcile_detects_missing_and_stale_jobs(db_session) -> None:
    synced, missing = await seed_acme_job(db_session), await seed_acme_job(db_session)
    graph = InMemoryGraphRepository()
    svc = sync_service(db_session, graph)
    await svc.sync_job(synced)
    report = await svc.reconcile(dry_run=True)
    states = {i.research_job_id: i.state for i in report.items}
    assert states == {synced: "in_sync", missing: "missing"}
    assert report.enqueued == 0
    # drop some graph claims of the synced job -> stale
    claims = [
        n.id
        for n in graph.nodes.values()
        if n.label is Label.CLAIM and n.properties["research_job_id"] == str(synced)
    ]
    await graph.delete_nodes(claims[:3])
    report = await svc.reconcile(dry_run=False)
    states = {i.research_job_id: (i.state, i.action) for i in report.items}
    assert states[synced] == ("stale", "enqueued") and states[missing] == (
        "missing",
        "enqueued",
    )
    runs = await svc.process_due()
    assert sorted(r.status for r in runs) == ["completed", "completed"]
    final = await svc.reconcile(dry_run=True)
    assert final.totals == {"in_sync": 2}


async def test_sync_disabled_and_missing_job(db_session) -> None:
    with pytest.raises(GraphSyncDisabledError):
        await sync_service(
            db_session, settings=Settings(graph_sync_enabled=False)
        ).sync_job(uuid4())
    with pytest.raises(ResourceNotFoundException):
        await sync_service(db_session).sync_job(uuid4())


# -- queries ---------------------------------------------------------------------------------


@pytest.fixture
async def queried(db_session):
    job = await seed_acme_job(db_session)
    graph = InMemoryGraphRepository()
    await sync_service(db_session, graph).sync_job(job)
    return graph, GraphQueryService(
        graph, Settings(graph_max_query_results=5, graph_max_traversal_depth=2)
    )


async def test_company_profile_and_product_queries(queried) -> None:
    graph, q = queried
    profile = await q.company_profile(ACME)
    assert profile["company"]["name"] == "Acme"
    (product,) = profile["products"]
    assert product["verified"] and product["claim_count"] >= 1
    pid = product["node"]["id"]
    assert {f["node"]["name"] for f in await q.product_features(pid)} == {
        "Visual workflow builder",
        "Prebuilt connectors",
    }
    assert {i["node"]["name"] for i in await q.product_integrations(pid)} == {
        "Slack",
        "Salesforce",
    }
    tech = profile["technologies"]
    assert tech and all(
        t["assertion"] == "inferred" and not t["verified"] for t in tech
    )
    py = next(
        n
        for n in graph.nodes.values()
        if n.label is Label.TECHNOLOGY and n.properties["canonical_name"] == "Python"
    )
    assert [c["node"]["id"] for c in await q.technology_companies(py.id)] == [ACME]
    feature = next(n for n in graph.nodes.values() if n.label is Label.FEATURE)
    assert [p["node"]["id"] for p in await q.feature_products(feature.id)] == [pid]


async def test_evidence_claims_and_sources(queried) -> None:
    graph, q = queried
    evidence = await q.evidence(ACME)
    assert evidence["claims"] and evidence["sources"]
    assert evidence["verified_claims"] == sum(c["verified"] for c in evidence["claims"])
    claim_id = evidence["claims"][0]["claim_id"]
    sources = await q.claim_sources(claim_id)
    assert (
        sources
        and sources[0]["url"].startswith("https://acme.com")
        and sources[0]["excerpt_verified"] is not None
    )
    rel_claims = await q.provenance.claims_for_relationship(
        Rel.OFFERS, ACME, (await q.company_products(ACME))[0]["node"]["id"]
    )
    assert rel_claims and all(c["predicate"] == "offers_product" for c in rel_claims)


async def test_limits_depth_and_validation(queried) -> None:
    graph, q = queried
    assert len(await q.entity_claims(ACME)) <= 5  # capped by GRAPH_MAX_QUERY_RESULTS
    assert len(await q.relationships(ACME, limit=1000)) <= 5
    explored = await q.explore(ACME, depth=2, limit=3)
    assert len(explored["edges"]) <= 3 and explored["truncated"]
    with pytest.raises(GraphValidationError):
        await q.explore(ACME, depth=3)  # above GRAPH_MAX_TRAVERSAL_DEPTH
    with pytest.raises(GraphValidationError):
        await q.relationships(ACME, types=["DETACH DELETE"])
    with pytest.raises(GraphValidationError):
        await q.search("Robot")
    with pytest.raises(ResourceNotFoundException):
        await q.company_profile(str(uuid4()))
    with pytest.raises(ResourceNotFoundException):
        await q.product_features(ACME)  # a company is not a product


async def test_compare_companies_uses_documented_overlap(db_session) -> None:
    graph = InMemoryGraphRepository()
    await sync_service(db_session, graph).sync_job(await seed_acme_job(db_session))
    q = GraphQueryService(graph, Settings())
    result = await q.compare_companies(ACME, ACME)
    tech = result["comparisons"][0]
    assert tech["jaccard"] == 1.0 and tech["counts"]["shared"] == tech["counts"]["a"]
    assert "jaccard" in result["measure"]


# -- Neo4j repository: Cypher construction safety ------------------------------------------------


class RecordingClient:
    def __init__(self, rows=None):
        self.calls, self.rows = [], rows or []

    async def execute_read(self, query, params):
        self.calls.append(("read", query, params))
        return self.rows

    async def execute_write(self, query, params):
        self.calls.append(("write", query, params))
        return [{"created": 1, "total": len(params.get("rows", [1])), "deleted": 0}]


async def test_cypher_is_parameterized_and_allowlisted() -> None:
    client = RecordingClient()
    repo = Neo4jGraphRepository(client)
    evil = "x'}) DETACH DELETE n //"
    await repo.upsert_nodes(
        Label.COMPANY,
        [
            {
                "id": evil,
                "props": {"canonical_name": evil},
                "lists": {},
                "status": "supported",
            }
        ],
    )
    await repo.upsert_edges(Rel.OFFERS, Label.COMPANY, Label.PRODUCT, [{"source_id": evil, "target_id": "p", "props": {},
                            "lists": {}, "status": "supported", "direct": True, "has_conflict": False}])  # fmt: skip
    await repo.edges(evil, rel_types=[Rel.OFFERS], neighbor_labels=[Label.PRODUCT])
    await repo.neighborhood(evil, depth=2)
    for _mode, query, params in client.calls:
        assert evil not in query  # values only travel as parameters
        assert evil in str(params)
        assert "apoc" not in query.lower()
    write_q = client.calls[0][1]
    assert "MERGE (n:GraphNode {id: row.id})" in write_q and "SET n:Company" in write_q
    assert [c[0] for c in client.calls[:2]] == ["write", "write"] and client.calls[2][
        0
    ] == "read"
    with pytest.raises(ValueError):
        await repo.upsert_nodes("Company) DETACH DELETE (n", [])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await repo.neighborhood("x", depth=99)
    with pytest.raises(ValueError):
        await repo.edges("x", rel_types=["NOT_A_TYPE"])  # type: ignore[list-item]


async def test_schema_initialization_is_idempotent_statements() -> None:
    client = RecordingClient()
    result = await Neo4jGraphRepository(client).initialize_schema()
    statements = [q for _, q, _ in client.calls]
    assert (
        all("IF NOT EXISTS" in s for s in statements[:-1])
        and "GraphSchemaVersion" in statements[-1]
    )
    assert result["version"] and not any("DROP" in s for s in statements)


# -- API --------------------------------------------------------------------------------------------


@pytest.fixture
async def api(db_session):
    graph = InMemoryGraphRepository()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_graph_sync_service] = lambda: sync_service(
        db_session, graph
    )
    app.dependency_overrides[get_graph_query_service] = lambda: GraphQueryService(
        graph, Settings()
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=AUTH_HEADERS
    ) as client:
        client.graph = graph  # type: ignore[attr-defined]
        yield client


async def test_api_sync_status_and_reads(api, db_session) -> None:
    job = await seed_acme_job(db_session)
    dry = await api.post(
        "/api/v1/graph/sync", json={"research_job_id": str(job), "dry_run": True}
    )
    assert dry.status_code == 200 and dry.json()["status"] == "dry_run"
    assert api.graph.nodes == {}  # type: ignore[attr-defined]
    status = (await api.get(f"/api/v1/graph/sync/status/{job}")).json()
    assert status["synchronized"] is False and status["outbox"] is None
    run = await api.post("/api/v1/graph/sync", json={"research_job_id": str(job)})
    assert run.status_code == 200 and run.json()["status"] == "completed"
    status = (await api.get(f"/api/v1/graph/sync/status/{job}")).json()
    assert (
        status["synchronized"] is True
        and status["recent_runs"][0]["status"] == "completed"
    )
    profile = await api.get(f"/api/v1/graph/companies/{ACME}")
    assert profile.status_code == 200 and profile.json()["products"]
    found = (
        await api.get(
            "/api/v1/graph/entities", params={"label": "Company", "name": "acme"}
        )
    ).json()
    assert found["items"][0]["id"] == ACME
    rels = await api.get(
        f"/api/v1/graph/entities/{ACME}/relationships",
        params={"types": ["OFFERS"], "direction": "out"},
    )
    assert rels.status_code == 200 and rels.json()["count"] == 1
    evidence = await api.get(f"/api/v1/graph/entities/{ACME}/evidence")
    assert evidence.status_code == 200 and evidence.json()["claims"]
    comp = (await api.get(f"/api/v1/graph/companies/{ACME}/competitors")).json()
    assert "inferred" in comp["note"]
    explore = await api.get(
        "/api/v1/graph/explore", params={"entity_id": ACME, "depth": 2, "limit": 10}
    )
    assert explore.status_code == 200 and len(explore.json()["edges"]) <= 10
    reconcile = (
        await api.post("/api/v1/graph/sync/reconcile", json={"dry_run": True})
    ).json()
    assert reconcile["totals"] == {"in_sync": 1}


async def test_api_errors(api) -> None:
    assert (await api.get(f"/api/v1/graph/entities/{uuid4()}")).status_code == 404
    bad_type = await api.get(
        f"/api/v1/graph/entities/{ACME}/relationships",
        params={"types": ["MATCH (n) DELETE n"]},
    )
    assert bad_type.status_code in (404, 422)
    assert (
        await api.get("/api/v1/graph/explore", params={"entity_id": ACME, "depth": 9})
    ).status_code == 422
    assert (
        await api.post("/api/v1/graph/sync", json={"research_job_id": "nope"})
    ).status_code == 422
    assert (
        await api.post(
            "/api/v1/graph/sync", json={"research_job_id": str(uuid4()), "cypher": "x"}
        )
    ).status_code == 422
    missing = await api.post(
        "/api/v1/graph/sync", json={"research_job_id": str(uuid4())}
    )
    assert missing.status_code == 404


async def test_api_without_neo4j_reports_not_configured(db_session) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=AUTH_HEADERS
    ) as client:
        health = (await client.get("/api/v1/graph/health")).json()
        assert health["status"] == "not_configured"
        read = await client.get(f"/api/v1/graph/companies/{ACME}")
        assert (
            read.status_code == 503
            and read.json()["error"]["code"] == "graph_not_configured"
        )
        job = await seed_acme_job(db_session)
        dry = await client.post(
            "/api/v1/graph/sync", json={"research_job_id": str(job), "dry_run": True}
        )
        assert dry.status_code == 200  # planning reads PostgreSQL only
        real = await client.post(
            "/api/v1/graph/sync", json={"research_job_id": str(job)}
        )
        assert real.status_code == 503 and "Traceback" not in real.text
