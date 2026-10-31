"""Opt-in live Neo4j tests: schema, sync and query parity with the in-memory store.

Skipped unless ``GRAPH_RUN_LIVE_TESTS=true`` and ``NEO4J_PASSWORD`` is configured
(read from the root ``.env``). Point it at a disposable Neo4j (e.g. the compose
``graph`` profile); the test deletes only the nodes it created, by id.
"""

import os

import pytest

from app.core.config import ENV_FILE, Settings
from app.graph.graph_query_service import GraphQueryService
from app.graph.graph_repository import InMemoryGraphRepository, Neo4jGraphRepository
from app.graph.neo4j_client import Neo4jClient
from app.graph.normalization import company_id_for_domain
from tests.analysis_support import seed_acme_job
from tests.graph_support import sync_service

LIVE = os.environ.get("GRAPH_RUN_LIVE_TESTS", "false").lower() == "true"
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not LIVE, reason="GRAPH_RUN_LIVE_TESTS is not 'true'"),
]


def edge_set(edges):
    return {
        (e.type.value, e.source_id, e.target_id, e.properties.get("role"),
         tuple(sorted(e.properties.get("claim_ids", []))), e.properties.get("verification_status"),
         e.properties.get("assertion"))
        for e in edges
    }  # fmt: skip


async def test_live_schema_sync_and_parity(db_session) -> None:
    settings = Settings(_env_file=ENV_FILE)  # type: ignore[call-arg]
    client = Neo4jClient(settings)
    if not client.is_configured:
        pytest.skip("NEO4J_PASSWORD is not set")
    live = Neo4jGraphRepository(client)
    memory = InMemoryGraphRepository()
    created: set[str] = set()
    try:
        await live.initialize_schema()
        await live.initialize_schema()  # idempotent
        schema = await live.verify_schema()
        assert (
            schema["missing_constraints"] == []
            and schema["missing_indexes"] == []
            and schema["version"]
        )

        job = await seed_acme_job(db_session)
        run_live = await sync_service(db_session, live, settings).sync_job(job)
        await sync_service(db_session, memory, settings).sync_job(job)
        created = set(memory.nodes)
        assert run_live.status == "completed" and run_live.counts.failed_records == 0

        live_nodes = await live.get_nodes(sorted(created))
        assert {(n.id, n.label) for n in live_nodes} == {
            (n.id, n.label) for n in memory.nodes.values()
        }
        live_edges = []
        for node_id in created:
            live_edges += [
                e for e, _ in await live.edges(node_id, direction="out", limit=1000)
            ]
        assert edge_set(live_edges) == edge_set(memory.edges_by_key.values())

        again = await sync_service(db_session, live, settings).sync_job(job)
        assert again.counts.entities_created == again.counts.relationships_created == 0

        acme = company_id_for_domain("acme.com")
        q_live, q_mem = (
            GraphQueryService(live, settings),
            GraphQueryService(memory, settings),
        )
        p_live, p_mem = (
            await q_live.company_profile(acme),
            await q_mem.company_profile(acme),
        )
        for key in ("products", "technologies", "integrations", "topics"):
            assert sorted(i["node"]["id"] for i in p_live[key]) == sorted(
                i["node"]["id"] for i in p_mem[key]
            )
        assert await live.count_job_claims(str(job)) == await memory.count_job_claims(
            str(job)
        )
        explored = await q_live.explore(acme, depth=2, limit=10)
        assert 0 < len(explored["edges"]) <= 10
    finally:
        if created:
            await live.delete_nodes(sorted(created))
        await client.close()
