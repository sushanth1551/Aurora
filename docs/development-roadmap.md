# Development roadmap

This roadmap is a proposed sequence, not a feature-completion claim.

1. **Foundation and Module 1 (complete):** settings, logging, API contracts, test
   baseline, health and research placeholder, skeletons for all modules.
2. **Persistence:** SQLAlchemy models, migrations, research-job lifecycle,
   repositories, and durable status APIs.
3. **Research ingestion:** safe URL policy, HTTPX/BeautifulSoup baseline,
   Firecrawl adapter, source provenance, and retrieval limits.
4. **SERP/competitor analysis:** provider adapter, cached/dated results, and
   intent/competitor summaries with confidence.
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
