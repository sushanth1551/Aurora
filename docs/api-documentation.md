# API documentation

Run the API and open `/docs` (Swagger UI) or `/redoc` for generated OpenAPI
docs. Every response carries an `X-Request-ID` header (a safe client-supplied
value is echoed, otherwise one is generated) that also appears in server logs.

## `GET /`
```json
{"application": "AURORA", "version": "0.1.0",
 "description": "Autonomous Web Research and SEO Intelligence Platform",
 "status": "running"}
```

## `GET /api/v1/health`
`{"status":"healthy","application":"AURORA","version":"0.1.0"}`. Liveness only;
does not contact the database or any provider.

## `POST /api/v1/research` — run a research job (synchronous)

Searches via SerpApi, validates URLs, extracts pages via Firecrawl, persists the
job and returns it. Needs `SERPAPI_API_KEY`, `FIRECRAWL_API_KEY` (when
`scrape_pages` is true) and a reachable PostgreSQL database. The request blocks
until completion or `RESEARCH_TIMEOUT_SECONDS` (default 60); see
`docs/research-engine.md`.

Request:

```json
{
  "query": "Best AI automation tools for businesses in India",
  "max_results": 10,
  "scrape_pages": true,
  "country": "in",
  "language": "en",
  "allowed_domains": ["example.com"],
  "request_id": "my-idempotency-key-001"
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `query` | string, required | 1–500 chars after trimming |
| `max_results` | int | 1–100, default 10; page budget is also capped by `RESEARCH_MAX_PAGES` |
| `scrape_pages` | bool | default `true`; `false` records sources as `skipped` |
| `country` | string | two letters (`gl`) |
| `language` | string | e.g. `en`, `pt-br` (`hl`) |
| `allowed_domains` | string[] | max 50 bare domains (subdomains match) |
| `request_id` | string | optional idempotency key, 8–128 chars `[A-Za-z0-9._:-]`; repeating it returns the same job without new provider calls |

Unknown fields are rejected (`422`).

Response (abridged):

```json
{
  "job_id": "5b0f6d3e-…",
  "query": "Best AI automation tools for businesses in India",
  "status": "partially_completed",
  "requested_result_count": 10,
  "actual_result_count": 10,
  "eligible_source_count": 10,
  "successful_source_count": 8,
  "failed_source_count": 2,
  "search_results": [{"position": 1, "title": "…", "url": "https://…",
                      "snippet": "…", "displayed_link": "…", "domain": "…",
                      "metadata": {"engine": "google", "search_id": "…"}}],
  "sources": [{
    "id": "…", "original_url": "https://…", "normalized_url": "https://…",
    "final_url": null, "domain": "example.com", "title": "…", "snippet": "…",
    "extracted_markdown": "# …", "extraction_status": "succeeded",
    "content_hash": "sha256 hex", "metadata": {"position": 1},
    "error_message": null, "created_at": "…", "extracted_at": "…"}],
  "stages": [{"stage": "search", "status": "succeeded", "started_at": "…",
              "duration_ms": 812, "item_count": 10, "error": null}],
  "errors": [{"stage": "extraction", "category": "partial_extraction",
              "message": "2 of 10 pages could not be extracted."}],
  "failure": null,
  "created_at": "…", "completed_at": "…"
}
```

`status`: `completed`, `partially_completed`, `failed` (never `running` or
`pending` after a synchronous call). `extraction_status`: `succeeded`, `failed`,
`rejected` (blocked by URL policy), `skipped`.

| HTTP | Meaning |
| --- | --- |
| 200 | `completed`, `partially_completed`, or a `failed` job whose cause is not an outage (e.g. `no_eligible_urls`, every page failed to extract). Check `status`/`failure`. |
| 422 | Invalid request body |
| 503 | A required service prevented the job: SerpApi/Firecrawl not configured, rejected credentials, rate limited or unavailable, deadline exceeded, or the database is unavailable. Body is the job (with `failure`) when a job was stored, otherwise `{"error": {...}}`. |
| 500 | Unexpected internal error (generic message only) |

## `GET /api/v1/research/{job_id}`

Returns the stored job exactly as above (its `stages` list the first three
stages). Query parameter `include_content=false` omits `extracted_markdown`.

* `404` `{"error": {"code": "resource_not_found", ...}}` if the id is unknown.
* `422` for an id that is not a UUID.
* `503` if the database is unavailable.

## Errors

Application errors use `{"error": {"code": "...", "message": "..."}}` and never
include stack traces, URLs with credentials, or connection strings.

## Planned
Pagination of large sources, a background-job model (`202` + polling),
authentication/authorization and rate limiting.

# Module 3: website intelligence endpoints

All are under `/api/v1/research`, **synchronous**, and documented in Swagger.
Details and rationale: `website-intelligence-engine.md`. Errors use the standard
`{"error": {...}}` body: `404` unknown job/website/report, `422` invalid input,
`502` an external provider failed (message carries no URLs/keys), `503` database or
required configuration unavailable.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/websites` | Discover websites; creates a job |
| POST | `/websites/{website_id}/map` | Map and classify a website's pages |
| POST | `/websites/{website_id}/crawl` | Crawl selected pages, extract, validate, update report |
| POST | `/intelligence` | Run the whole workflow |
| GET | `/{job_id}/progress` | Status, stage timings, counts, report availability |
| GET | `/{job_id}/websites` | Discovered websites (paginated) |
| GET | `/{job_id}/entities` | Resolved entities (paginated, `entity_type` filter) |
| GET | `/{job_id}/claims` | Evidence-backed claims (paginated, `status`, `predicate`) |
| GET | `/{job_id}/report` | Report JSON, or `?format=markdown` (`report_type` filter) |

Pagination: `limit` (1-200, default 50) and `offset` (>= 0); responses are
`{"items": [...], "total": n, "limit": n, "offset": n}`.

## Examples

```http
POST /api/v1/research/intelligence
{"query": "Acme", "subject_type": "company", "target_names": ["Acme", "Globex"],
 "country": "in", "language": "en", "max_websites": 5, "max_pages_per_site": 10,
 "report_type": "competitor_profile"}
```
`website_urls` (max 20 public URLs) skips discovery; those sites stay `unverified`
until homepage branding confirms them. Response (summary):

```json
{"job_id": "...", "status": "completed", "websites": 2, "mapped_pages": 31,
 "crawled_pages": 14, "failed_pages": 1, "documents": 14, "entities": 62,
 "claims": 118,
 "coverage": {"total_claims": 118, "supported": 71, "partially_supported": 30,
              "conflicting": 2, "unsupported": 0, "unverified": 15,
              "coverage_ratio": 0.856, "pricing_found": true},
 "stages": [{"stage": "discovery", "status": "succeeded", "duration_ms": 910}],
 "errors": [], "notes": ["1 third-party site(s) ... were excluded"],
 "report_available": true}
```

```http
POST /api/v1/research/websites            {"query": "Acme", "target_names": ["Acme"]}
POST /api/v1/research/websites/{id}/map   {"max_urls": 100, "search": "pricing"}
POST /api/v1/research/websites/{id}/crawl {"max_pages": 8, "page_types": ["pricing", "product"]}
GET  /api/v1/research/{job_id}/claims?status=conflicting&predicate=headquarters
GET  /api/v1/research/{job_id}/report?format=markdown
```
A claim looks like:

```json
{"claim_id": "...", "subject_entity": "Acme", "predicate": "headquarters",
 "object_value": "Bengaluru, India", "source_url": "https://acme.com/",
 "source_domain": "acme.com", "source_kind": "official",
 "evidence_excerpt": "We are headquartered in Bengaluru, India.",
 "extraction_method": "deterministic", "verification_status": "supported",
 "confidence_score": 0.7, "observed_at": "...", "notes": []}
```

`GET /api/v1/research/{job_id}` (Module 2) also works for intelligence jobs; their
in-progress statuses are reported as `running`.

## Intelligence synthesis (Module 4) — `/api/v1/intelligence`

Analyses an existing research job (Module 2 or 3) with an LLM. Runs **synchronously**,
bounded by `INTELLIGENCE_MAX_DURATION_SECONDS`; progress is committed per stage, so
`GET /intelligence/{job_id}` can be polled from another client. With the default
`LLM_PROVIDER=mock` no key is needed, but the report is marked as mock output and
`requires_review`.

### `POST /api/v1/intelligence/analyze`

```json
{
  "research_job_id": "6f1c...",
  "research_objective": "Compare the products, pricing and target customers of these AI automation companies",
  "analysis_type": "competitor_intelligence",
  "preferred_provider": null,
  "preferred_model": null,
  "maximum_iterations": 2,
  "maximum_sources": 20,
  "output_format": "both",
  "allow_follow_up_research": false,
  "research_configuration": {"country": "in", "language": "en"}
}
```

`analysis_type` can be `executive_summary`, `detailed_report` (default),
`company_intelligence`, `product_comparison`, `technology_analysis`,
`competitor_intelligence` or `market_research`. `maximum_*` can only lower the server
limits. Unknown fields such as a system prompt are rejected (422).
`allow_follow_up_research` also needs `INTELLIGENCE_ENABLE_FOLLOWUP_RESEARCH=true`, and it
spends SerpApi/Firecrawl credits. `preferred_model` must be the configured model or have
a price in `LLM_PRICING_JSON`.

Response:

```json
{"analysis_id": "...", "research_job_id": "...", "analysis_type": "competitor_intelligence",
 "status": "requires_review", "provider": "anthropic", "model": "<model>",
 "report_status": "requires_review", "report_available": true,
 "progress": {"stage": "completed", "iterations": 1, "follow_up_queries": 0,
              "evidence_items": 27, "sources": 4, "llm_calls": 5, "tokens_used": 6400,
              "estimated_cost": null, "cost_complete": false,
              "stop_reason": "sufficient", "degraded": false,
              "notes": ["1 unsupported factual statement(s)"]},
 "error": null, "created_at": "...", "completed_at": "..."}
```

Statuses: `pending`, `running`, `completed`, `partially_completed` (LLM unavailable or over
budget for part of the run; deterministic parts delivered), `failed` (no usable evidence,
deadline, provider/configuration error) and `requires_review` (a quality gate did not pass).
Errors: 404 unknown job, 422 invalid body, 503 provider not configured (for example
`LLM_PROVIDER=openai` without `OPENAI_API_KEY`/`OPENAI_MODEL`) or database unavailable.

### Read endpoints
```http
GET /api/v1/intelligence/{job_id}                            # latest analysis (+ other_analysis_ids)
GET /api/v1/intelligence/{job_id}/report?format=json         # IntelligenceReport
GET /api/v1/intelligence/{job_id}/report?format=markdown     # text/markdown
GET /api/v1/intelligence/{job_id}/usage                      # per-request tokens, estimated cost
GET /api/v1/intelligence/{job_id}/quality                    # ReportQualityMetrics + gate result
```
Each one accepts `?analysis_id=` to select an analysis other than the latest. Report
citations are evidence ids (`c-…` for a stored claim, `s-…` for a stored source excerpt)
listed in `sources` with their stored URL and excerpt. See `report-generation.md` and
`research-quality-evaluation.md`.

## Knowledge graph (Module 5): `/api/v1/graph`

Neo4j is optional. Without `NEO4J_PASSWORD`, `GET /graph/health` returns
`{"status":"not_configured"}` and graph reads return `503 graph_not_configured`;
`POST /graph/sync` with `"dry_run": true` still works (it reads PostgreSQL only).

```http
GET  /api/v1/graph/health                                 # connected | disconnected | unavailable | not_configured
POST /api/v1/graph/sync            {"research_job_id": "...", "dry_run": false}
POST /api/v1/graph/sync/reconcile  {"dry_run": true}
POST /api/v1/graph/sync/process    {"limit": 20}           # pending + due failed events
POST /api/v1/graph/sync/retry      {"limit": 20}           # due failed events only
GET  /api/v1/graph/sync/status/{job_id}                   # "synchronized" true only after Neo4j confirmed
GET  /api/v1/graph/review                                 # entity matches kept separate for review
GET  /api/v1/graph/entities?label=Company&name=acme
GET  /api/v1/graph/entities/{id}  |  /relationships  |  /evidence  |  /claims
GET  /api/v1/graph/companies/{id}  |  /products  |  /services  |  /competitors  |  /network
GET  /api/v1/graph/companies/{a}/compare/{b}
GET  /api/v1/graph/products/{id}/technologies  |  /features  |  /integrations  |  /competitors
GET  /api/v1/graph/products/{a}/compare/{b}
GET  /api/v1/graph/technologies/{id}/companies  |  /network
GET  /api/v1/graph/features/{id}/products   /articles/{id}/topics   /topics/{id}/articles   /claims/{id}/sources
GET  /api/v1/graph/explore?entity_id=...&depth=2&types=OFFERS&types=USES&limit=50
```

A sync run returns `{"run_id", "status": "completed|partially_completed|failed|dry_run", "counts": {...},
"rejected": [...], "review": [...]}`. Errors: 404 unknown job/entity; 422 unknown label/type,
depth above `GRAPH_MAX_TRAVERSAL_DEPTH`, extra body fields; 503 Neo4j not configured/unavailable or
sync disabled. Details: `graph-query-guide.md`, `graph-synchronization.md`.
