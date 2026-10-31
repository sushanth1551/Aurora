# Database design

## Status
**PostgreSQL (Module 2) is implemented** for the research engine: SQLAlchemy 2.x
async models, an Alembic migration, and a repository. **Neo4j and the vector
store are not implemented** (`Neo4jClient.connect` raises). The API starts
without any database; a database is only needed when a research request is
made. Missing/unreachable databases yield `503 persistence_error`.

## Implemented schema (research engine)

```mermaid
erDiagram
    research_jobs ||--o{ search_results : "has"
    research_jobs ||--o{ research_sources : "has"
    research_jobs {
        uuid id PK
        string request_id UK "idempotency key, nullable"
        string query
        string status
        int max_results
        bool scrape_pages
        string country
        string language
        jsonb allowed_domains
        int total_search_results
        int eligible_source_count
        int successful_source_count
        int failed_source_count
        string error_category
        text error_summary
        jsonb errors
        jsonb stage_summaries
        timestamptz created_at
        timestamptz started_at
        timestamptz completed_at
    }
    search_results {
        uuid id PK
        uuid research_job_id FK
        int position
        text title
        text url
        text snippet
        text displayed_link
        string domain
        jsonb provider_metadata
        timestamptz created_at
    }
    research_sources {
        uuid id PK
        uuid research_job_id FK
        text original_url
        text normalized_url
        text final_url
        string domain
        text title
        text snippet
        text extracted_markdown
        string extraction_status
        string content_hash
        jsonb provider_metadata
        text error_message
        timestamptz created_at
        timestamptz extracted_at
    }
```

* Foreign keys are `NOT NULL` with `ON DELETE CASCADE`: a source cannot exist
  without its job.
* `UNIQUE (research_job_id, normalized_url)` prevents duplicate sources in a job
  (rejected URLs with no normalized form are exempt: PostgreSQL treats NULLs as
  distinct).
* Indexes: job `status`, `created_at`; child `research_job_id`; `domain`;
  source `normalized_url`, `content_hash`, `extraction_status`.
* Statuses are stored as strings (not PostgreSQL enums) so adding a status needs
  no enum migration; the application validates them.
* `content_hash` is a SHA-256 of the extracted Markdown, ready for cross-job
  caching. No credentials are ever stored.
* The job row is inserted (`running`) before provider calls; search results,
  sources and the final job state are then written in **one transaction**.

## Connection lifecycle
`database/session.py` creates the async engine lazily (first request), with
`pool_pre_ping`, pool limits and a connect timeout; it is disposed in the
FastAPI lifespan shutdown. Repositories translate driver errors into
`PersistenceException` with a safe message.

## Migrations (Alembic)
Configuration: `alembic.ini` + `backend/migrations/`. The URL comes from
`DATABASE_URL`. Run from the repository root with `PYTHONPATH=backend`:

```powershell
$env:PYTHONPATH = "backend"
alembic upgrade head                                        # apply
alembic downgrade -1                                        # roll back one step (drops tables!)
alembic revision -m "describe change"                       # new empty revision
alembic revision --autogenerate -m "describe change"        # compare models to a live DB
alembic upgrade head --sql                                  # print SQL, no connection
alembic current; alembic history
```
Migrations are never run automatically. `downgrade` drops tables and their data.

## Further PostgreSQL tables (planned)
Operational relational data should additionally include:
- `findings`: normalized claim/text, confidence, and source references.
- `keyword_clusters`, `content_briefs`, and `content_drafts`.
- `audit_events`: actor, action, target, timestamp, and approval state.

Use UUID identifiers, UTC timestamps, explicit lifecycle enums, foreign keys,
unique constraints for idempotency, and migrations. Keep large raw source
payloads in controlled object storage when required rather than unbounded rows.

## Neo4j
See [knowledge-graph-schema.md](./knowledge-graph-schema.md) for node and
relationship conventions.

## Vector storage
ChromaDB is the initial dependency choice for a local vector-store adapter.
Store embeddings with chunk/source IDs and metadata; do not treat vectors as a
system of record. Re-index from canonical source records when embeddings or
chunk policies change.

## Security and operations
Use separate least-privilege credentials, TLS in production, encrypted
backups, retention limits, and migration review. Never commit credentials.
Database startup must be explicit; importing the API must not require running
databases.

## Module 3 schema (migration `0002`, additive)

```mermaid
erDiagram
    research_jobs ||--o{ research_websites : has
    research_websites ||--o{ mapped_pages : has
    research_jobs ||--o{ research_sources : has
    research_sources ||--o| processed_documents : "normalized as"
    research_jobs ||--o{ research_entities : has
    research_entities ||--o{ research_claims : "subject of"
    research_sources ||--o{ research_claims : "evidence from"
    research_jobs ||--o{ research_reports : has
    research_websites {
        uuid id PK
        uuid research_job_id FK
        string website_name
        string domain "unique per job"
        text homepage_url
        string official_status
        string website_type
        float relevance_score
        jsonb discovery_metadata "full WebsiteCandidate"
    }
    mapped_pages {
        uuid id PK
        uuid website_id FK
        text normalized_url "unique per website"
        string page_type
        float relevance_score
        bool crawl_eligible
        string crawl_status "pending|crawled|failed|skipped|excluded"
        string exclusion_reason
    }
    processed_documents {
        uuid id PK
        uuid research_job_id FK
        uuid source_id FK
        string page_type
        string original_hash
        text normalized_content
        string content_hash
        jsonb sections
        jsonb document_metadata
        string processing_status
    }
    research_entities {
        uuid id PK
        uuid research_job_id FK
        uuid entity_key "stable UUIDv5, unique per job"
        string entity_type
        text canonical_name
        string normalized_name
        string scope_domain
        jsonb attributes
        float confidence
    }
    research_claims {
        uuid id PK
        uuid research_job_id FK
        uuid entity_id FK "SET NULL"
        uuid source_id FK "SET NULL"
        string predicate
        text object_value
        text source_url
        string source_kind
        text evidence_excerpt
        string verification_status
        float confidence
        timestamptz observed_at
    }
    research_reports {
        uuid id PK
        uuid research_job_id FK
        string report_type "unique per job"
        jsonb content
        text markdown
    }
```

* `research_jobs.job_type` (`research` default, `intelligence`) is the only change
  to a Module 2 table; existing rows keep `research`. Module 3 reuses
  `research_sources` (the original extracted Markdown lives there) and
  `search_results` (raw discovery results).
* A processed document's *original content reference* is its `source_id` plus
  `original_hash`; the normalized text is stored on the document.
* `research_entities.id` is per job; `entity_key` is the stable cross-run id
  exposed by the API.
* The migration only creates objects and adds one defaulted column. Its downgrade
  drops only Module 3 objects (verified from generated SQL; run against a live
  PostgreSQL only via the opt-in integration tests).
* Commands: `alembic upgrade head`, `alembic downgrade 0001` (drops Module 3 data
  only), `alembic upgrade head --sql` (print SQL, no connection).
