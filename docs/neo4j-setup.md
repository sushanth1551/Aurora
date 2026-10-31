# Neo4j setup (local development)

Supported: Neo4j **5.x Community** (compose image `neo4j:5-community`) with the official Python
driver `neo4j` 5.28 (already in `requirements.txt` and the venv). No APOC or plugins. Enterprise-only
features (node keys, existence constraints, multiple user databases) are not used.

## 1. Configure (root `.env`, never committed)

```
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<choose one; also used by compose on first start>
NEO4J_DATABASE=neo4j
```

Without `NEO4J_PASSWORD` the API still starts. `GET /api/v1/graph/health` reports `not_configured`,
graph reads return `503 graph_not_configured`, and `POST /graph/sync` with `dry_run` still works.

## 2. Start the services (manual; nothing starts containers for you)

```powershell
docker compose up -d postgres
docker compose --profile graph up -d neo4j      # browser: http://localhost:7474  bolt: bolt://localhost:7687
```

Host ports can be changed with `NEO4J_HTTP_PORT` / `NEO4J_BOLT_PORT`. Data persists in the
`neo4j_data` volume.

## 3. Migrate, initialize, verify (repository root, `$env:PYTHONPATH = "backend"`)

```powershell
alembic upgrade head                              # adds the Module 5 tables (0004, additive)
python scripts/graph_admin.py health              # "connected" only after a verified query
python scripts/graph_admin.py init-schema         # idempotent constraints + indexes + version node
python scripts/graph_admin.py verify-schema       # exit 1 if anything is missing
```

## 4. Synchronize

```powershell
python scripts/graph_admin.py sync --job-id <uuid> --dry-run   # plan only
python scripts/graph_admin.py sync --job-id <uuid>
python scripts/graph_admin.py sync-all            # every job with entities/claims
python scripts/graph_admin.py reconcile           # report drift; add --apply to enqueue
python scripts/graph_admin.py sync-incremental    # process pending / due failed events
```

## 5. Inspect (Neo4j Browser)

```cypher
MATCH (c:Company)-[r]->(x) RETURN c, r, x LIMIT 50;
MATCH (s:Source)-[:SUPPORTS]->(cl:Claim)-[:MENTIONS]->(p:Product) RETURN s.url, cl.claim_text, cl.verification_status, p.canonical_name LIMIT 25;
MATCH ()-[r]->() WHERE r.assertion = 'inferred' RETURN type(r), r.basis, count(*);
```

## 6. Tests

```powershell
python -m pytest backend/tests -k graph                       # offline: mocks and in-memory store
$env:GRAPH_RUN_LIVE_TESTS = "true"
python -m pytest backend/tests/test_graph_live.py -v          # needs a disposable Neo4j; deletes only its own nodes
```

There is no command that drops or resets the graph. `graph_admin.py cleanup-test --ids-file f
--confirm` deletes only the listed node ids (test use). To discard everything, remove the
container volume yourself.
