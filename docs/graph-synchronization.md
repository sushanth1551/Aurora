# Graph synchronization (PostgreSQL -> Neo4j)

PostgreSQL and Neo4j share no transaction, so the sync is **eventually consistent** and every
write is an idempotent MERGE upsert keyed by stable ids.

```mermaid
sequenceDiagram
    participant API as API / CLI
    participant S as GraphSyncService
    participant PG as PostgreSQL
    participant N as Neo4j
    API->>S: sync_job(job, dry_run?)
    S->>PG: load entities, claims, sources, websites (read only)
    S->>PG: prior resolutions (other jobs)
    S->>S: extract -> resolve -> relationships -> provenance
    alt dry_run
        S-->>API: planned counts, rejected rows, review candidates (no writes)
    else write
        S->>PG: outbox(job) = processing, attempt += 1; run = running
        loop batches of GRAPH_SYNC_BATCH_SIZE
            S->>N: UNWIND rows MERGE ... (write tx, timeout, bounded retries)
        end
        alt all batches confirmed
            S->>PG: save resolutions; outbox = succeeded; run = completed
        else error
            S->>PG: outbox = failed (next_retry_at = 30s x 2^(n-1), max 1h) or dead; run = failed
        end
    end
```

**Outbox** (`graph_sync_outbox`): one row per `(research_job, project_job)`. `enqueue` is
idempotent. Statuses: `pending -> processing -> succeeded | failed | dead`. Payloads are references
(`research_jobs/<id>`), never document content. `dead` means `attempt_count` exceeded
`GRAPH_SYNC_MAX_RETRIES + 1`; it is not retried automatically but reconciliation re-enqueues it.

**Retries:** each batch goes through `retry_async`. Only transient errors are retried
(`GraphUnavailableError`: connection, database unavailable, transient Neo4j errors), up to
`GRAPH_SYNC_MAX_RETRIES` with jittered backoff. Query or constraint errors and authentication
failures are not retried. The driver additionally retries transient errors inside each managed
transaction, bounded by `NEO4J_QUERY_TIMEOUT_SECONDS`.

**Modes:**

| Mode | Entry point |
| --- | --- |
| Per job | `POST /graph/sync`, `graph_admin.py sync --job-id` |
| Full | `graph_admin.py sync-all` (every job with entities or claims) |
| Incremental | `POST /graph/sync/process`, `graph_admin.py sync-incremental` (pending + due failed) |
| Retry failed | `POST /graph/sync/retry`, `graph_admin.py retry-failed` |
| Reconciliation | `POST /graph/sync/reconcile`, `graph_admin.py reconcile [--apply]` |
| Dry run | `dry_run: true` on sync / reconcile; needs no Neo4j |

**Reconciliation** compares, per eligible job, the PostgreSQL claim count with
`count(Claim {research_job_id})` in Neo4j and the outbox status. States: `in_sync`, `missing`
(never synced), `stale` (graph has fewer claims), `failed`, `dead`, `pending`, `processing`
(interrupted run), `unknown` (no graph configured). Unless it is a dry run, `missing`, `stale`,
`failed`, `dead` and `processing` jobs are re-enqueued.

**Concurrency:** uniqueness constraints make concurrent MERGEs of the same id safe (a losing
transaction gets a transient error and is retried). Re-running a job after a crash between the
Neo4j write and the PostgreSQL commit converges to the same graph.

**Metrics** (`graph_sync_runs.counts`, also logged as `graph_sync_completed`): entities processed /
created / updated, duplicates detected, review candidates, relationships created / updated /
rejected, claims linked, source and claim nodes created / updated, retries, and failed records (edge
endpoints missing). Logs never contain credentials, Cypher parameters or source text.

Not done in Module 5: automatic enqueue when a Module 3 job finishes (left out to avoid changing
Module 3; run `sync-incremental` or reconcile), deletes and propagation of PostgreSQL record removal,
and a background worker (no message broker by design).
