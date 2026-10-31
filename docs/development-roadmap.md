# Development roadmap

This roadmap is a proposed sequence, not a feature-completion claim.

1. **Foundation and Module 1 (complete):** settings, logging, API contracts, test
   baseline, health and research placeholder, skeletons for all modules.
2. **Persistence (done in Module 2):** SQLAlchemy models, Alembic migration,
   research-job lifecycle, repository, and a retrieval API.
3. **Research ingestion (done in Module 2):** URL/SSRF policy, Firecrawl
   adapter, source provenance, and retrieval limits. Pending: live provider
   verification, background jobs, caching across jobs.
4. **SERP/competitor analysis** (SerpApi adapter exists; website discovery and competitor comparison reports were added in Module 3): provider adapter, cached/dated results, and
   intent/competitor summaries with confidence.
4b. **AI research and intelligence synthesis (done in Module 4):** LLM provider
   layer with usage/cost tracking, evidence context, bounded reasoning with
   controlled follow-up research, synthesis, cited reports and quality gates.
   Pending: live LLM verification, background execution, response caching.
5. **Knowledge graph:** Neo4j constraints, schema validation, entity resolution,
   idempotent writes, and provenance.
6. **RAG:** document processing, chunk/embedding strategy, vector adapter,
   graph/vector retrieval, and grounded answer evaluation.
7. **SEO planning and content:** keyword research, briefs, source-grounded
   writing, factual verification, and auditable quality reports.
8. **Publication and monitoring:** approval workflow, secure WordPress drafts,
   authorized Search Console reporting, and operational observability.

Each phase needs tests, data/security review, documentation, and a clear feature
status. External integrations should be opt-in and testable with mocked clients.
