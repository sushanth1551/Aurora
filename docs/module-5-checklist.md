# Module 5 checklist — Knowledge graph and graph intelligence

**Date:** 2026-10-31. Ticked only when verified by an executed check. **No live Neo4j was
available**: Neo4j was not running and no container was started (that needs your approval), so
every Neo4j code path is verified with a fake driver, Cypher-construction tests and the in-memory
store. The live parity test is written and skipped.

## Milestones
- [x] 5.1 Neo4j client, config, exceptions, health (`neo4j_client.py`, `graph_config.py`, `graph_exceptions.py`, `graph_health.py`)
- [x] 5.2 Schema: 11 labels, 15 relationship types, registry, constraints, indexes, version (`graph_schema.py`)
- [x] 5.3 Entity extraction and normalization (`entity_extractor.py`, `normalization.py`)
- [x] 5.4 Entity resolution, 6 stages, review candidates, optional LLM stage (`entity_resolver.py`)
- [x] 5.5 Relationship extraction and validation (`relationship_extractor.py`)
- [x] 5.6 Provenance building and traversal (`provenance_manager.py`)
- [x] 5.7 Persistence and sync with outbox, retries, reconciliation, dry run (`graph_repository.py`, `graph_sync_service.py`, `graph_sync_repository.py`)
- [x] 5.8 Query and analytics service (`graph_query_service.py`)
- [x] 5.9 FastAPI: 30 endpoints under `/api/v1/graph` (`api/routes/graph.py`)
- [x] 5.10 Schema management: idempotent init/verify/inspect, version node, `scripts/graph_admin.py`; migration `0004`
- [x] 5.11 Tests (unit, API, PostgreSQL, opt-in live)
- [x] 5.12 Observability: run metrics table and structured logs
- [x] 5.13 Configuration (`NEO4J_*`, `GRAPH_*`), compose ports, `.env.example`

## Acceptance criteria
| # | Criterion | Status |
| --- | --- | --- |
| 1 | Modules 1-4 keep working | Verified (full suite) |
| 2 | Neo4j connection implemented | Implemented; fake-driver verified; **live not run** |
| 3 | Environment-driven config | Verified |
| 4 | Schema documented | Verified (`knowledge-graph-schema.md`) |
| 5-6 | Node and relationship types exist | Verified (registry test covers all required triples) |
| 7 | Uniqueness constraints initialized | Statements verified (idempotent, Community-safe); **not applied to a live Neo4j** |
| 8 | Stable PostgreSQL ids reused | Verified (entity keys, claim ids, source record ids) |
| 9 | Duplicates handled | Verified (cross-job sync of two jobs gives one Company and one Source per URL) |
| 10 | Ambiguous entities not merged | Verified (cross-domain company, cross-site product, fuzzy review band, LLM threshold) |
| 11-12 | Relationship extraction; invalid rejected | Verified |
| 13-15 | Provenance; claims to sources; entities to claims | Verified |
| 16 | PostgreSQL -> graph sync works | Verified against the in-memory store and on PostgreSQL bookkeeping; **Neo4j write path not run live** |
| 17 | Idempotent | Verified (second sync creates 0 nodes and 0 edges) |
| 18 | Failed sync retryable | Verified (outage -> failed + backoff -> retry -> succeeded; permanent -> dead) |
| 19 | Reconciliation detects missing records | Verified (missing and stale jobs) |
| 20 | Queries return correct relationships | Verified on the in-memory store; parity with Neo4j **pending** |
| 21 | Traversal limits | Verified |
| 22 | Graph APIs work | Verified (ASGI client) |
| 23 | Health accurate | Verified (connected only after a verified query) |
| 24 | Safe schema initialization | Verified (no DROP, IF NOT EXISTS, no automatic startup init) |
| 25 | Unit tests without Neo4j | Verified |
| 26 | Integration tests on an isolated graph DB | Written (`test_graph_live.py`); **not run** |
| 27 | Regression tests pass | Verified |
| 28 | Docs explain setup and operations | Verified |
| 29 | Graph traceable to evidence | Verified (every fact edge resolves to stored claims and job ids) |
| 30 | Unsupported facts never verified | Verified (no edge from unsupported claims; inferred edges never `verified`) |

## Validation results (executed)
- `pytest`: **667 passed, 15 skipped, 0 failed**. Skipped: 3 live LLM, 3 live SerpApi/Firecrawl,
  1 live Neo4j, 8 PostgreSQL (run separately below). Module 5 added 45 offline tests
  (`-k graph`: 45 passed, 2 skipped = the live Neo4j and PostgreSQL graph tests).
- `pip check`: clean. Frontend `npm run build`: succeeded (frontend unchanged).
- PostgreSQL (`-m postgres`, disposable `aurora_test_m5`, created and dropped): **8 passed**,
  including migration `0004` and graph-sync bookkeeping on PostgreSQL.
- `ruff check`, `ruff format --check`: clean. `mypy`: no issues (139 files). `docker compose config`: valid.
- Smoke run on seeded Module 3 data: 37 nodes, 66 relationships, 23 claims linked, 0 rejected;
  re-sync created nothing; reconcile 23/23 claims in sync.

## Decisions that differ from the brief
- Company ids derive from the verified official domain, so a company merges across jobs; other
  entities reuse Module 3 ids. Source nodes are one per normalized URL.
- `mentions_technology` -> `USES` is **inferred**; industry overlap -> `COMPETES_WITH` is
  **inferred** (confidence 0.3). There is no source of documented competition yet.
- Module 4 findings are LLM proposals, so they are not projected as graph facts.
- Keyword and Article have schema, constraints, registry and query support, but no producer (data
  comes in Modules 6-7). The extracted Topic comes from industry claims.
- `Company` is an allowed source for `USES` / `INTEGRATES_WITH`, and `MENTIONS` may target
  Integration and Topic, because Module 3 data attaches them that way.
- Sync is triggered via API/CLI or reconciliation; Module 3 does not auto-enqueue (Module 3 left
  unchanged). No background worker or broker.
- Tests live in the flat `backend/tests/` layout.
- The `relationship_manager.py` skeleton was removed (replaced by the registry and repository).

## Known limitations / pending
- **Live Neo4j verification pending**: Cypher has never executed against a server.
  `SHOW CONSTRAINTS` / `SHOW INDEXES` inside a read transaction and the dynamic `$rank[...]` map
  lookup are the parts most worth confirming.
- PostgreSQL deletions are not propagated to the graph.
- Name search uses `CONTAINS` (label scan bounded by `LIMIT`).
- The LLM resolution stage is implemented and unit-tested with a fake; it is off by default and not
  wired to a configured provider in the API.

## Manual steps
1. Set `NEO4J_PASSWORD` in `.env`; approve and run `docker compose --profile graph up -d neo4j`.
2. `alembic upgrade head`; `python scripts/graph_admin.py init-schema` then `verify-schema`.
3. `GRAPH_RUN_LIVE_TESTS=true python -m pytest backend/tests/test_graph_live.py -v`.
4. `python scripts/graph_admin.py sync-all --dry-run`, then without `--dry-run`.
