# Module 3 checklist — website intelligence engine

**Date:** 2026-10-31. Items are ticked only when verified by an executed check.
"Pending" needs API keys and/or a running PostgreSQL that were not available.

## Milestones
- [x] 3.1 Website discovery (`website_discovery.py`)
- [x] 3.2 Website mapping and page classification (`website_mapper.py`, `FirecrawlTool.map_site`)
- [x] 3.3 Intelligent crawling (`intelligent_crawler.py`, `robots.py`, `FirecrawlTool.batch_scrape`)
- [x] 3.4 Content processing (`content_processor.py`)
- [x] 3.5 Structured extraction (`structured_extractor.py`, `prompts/extraction_prompts.py`)
- [x] 3.6 Entity resolution (`entity_resolver.py`)
- [x] 3.7 Evidence and validation (`evidence_manager.py`)
- [x] 3.8 Reports (`research_report.py`)
- [x] 3.9 PostgreSQL persistence code + migration `0002` (SQL generated offline; **not applied to a live PostgreSQL**)
- [x] 3.10 API endpoints (`api/routes/intelligence.py`)
- [x] 3.11 Orchestration (`website_intelligence_service.py`)

## Acceptance criteria
| # | Criterion | Status |
| --- | --- | --- |
| 1 | Module 2 functionality still works | Verified (all Module 2 tests pass; Module 2 GET works for Module 3 jobs) |
| 2 | Websites discovered from a query | Verified (mocked SerpApi); live **pending** |
| 3 | Official websites not assumed | Verified (look-alikes, first result, homepage confirmation) |
| 4 | Websites mapped into URLs | Verified (mocked Firecrawl map); live **pending** |
| 5 | Pages classified | Verified (21 classification cases) |
| 6 | Relevant pages selected | Verified |
| 7 | Firecrawl extracts permitted public pages | Mocked only; live **pending** |
| 8 | Crawling bounded and configurable | Verified |
| 9 | Content normalized | Verified |
| 10 | Duplicate URLs and content detected | Verified |
| 11 | Company info extracted | Verified (deterministic, mocked pages) |
| 12 | Product info extracted | Verified |
| 13 | Pricing only with evidence | Verified (no price invented; not-found reporting) |
| 14 | Technologies and features identified | Verified (technologies recorded as *mentions*) |
| 15 | Entities normalized and deduplicated | Verified |
| 16 | Claims preserve sources | Verified (verbatim excerpt checked against stored source) |
| 17 | Contradictions recorded | Verified (end-to-end) |
| 18 | Reports in JSON and Markdown | Verified |
| 19 | Data persisted in PostgreSQL | Verified on SQLite; **PostgreSQL not run** |
| 20 | API endpoints valid | Verified (ASGI tests + live server smoke test) |
| 21 | Tests pass without live credentials | Verified |
| 22 | Live tests remain optional | Verified (skipped by default) |
| 23 | Security controls documented and tested | Verified for the implemented controls; provider-side limits documented |
| 24 | Module 2 regression tests pass | Verified |

## Validation results (executed)
- `compileall`: OK. `ruff check`: clean. `ruff format --check`: 127 files formatted.
- `mypy`: no issues in 95 source files. `pip check`: no broken requirements.
- `pytest`: **431 passed, 8 skipped, 0 failed** (439 collected). Skipped: 5 from
  Module 2 (3 PostgreSQL, 2 live) and 3 new (2 PostgreSQL, 1 live Firecrawl).
  Module 3 added 219 passing tests.
- `alembic heads` -> `0002`; `upgrade head --sql` creates 9 tables (+ version
  table); offline tests compare the SQL to the ORM models and check the 0002
  upgrade is additive and its downgrade only drops Module 3 objects.
- Live server smoke test: new routes registered in OpenAPI; invalid body -> 422;
  everything that needs the database -> clean `503 persistence_error` (no secrets
  in response or log) while PostgreSQL is not running.
- `docker compose config` valid (containers/images **not built or run**);
  `npm run build` succeeds (the frontend was not changed).

## Decisions that differ from the brief
- **No Firecrawl `/v1/crawl`.** It follows links provider-side, which would bypass
  per-URL validation; mapping + our own selection + batch scrape is used instead.
  "Crawl depth" is path depth during mapping.
- **No background-job mechanism.** Runs are synchronous with a deadline, completed
  websites are persisted as they finish, and staged endpoints exist for large jobs.
- **No LLM extraction is wired**: the Groq/Claude adapters are still skeletons. The
  validated LLM interface and prompt-injection-safe prompt builder exist and are
  tested with a fake client.
- **Industry queries** do not produce company lists from prose; use `target_names`
  or `website_urls` to research specific companies.

## Known limitations
- Firecrawl `/v1/map` and `/v1/batch/scrape` request/response shapes come from the
  installed SDK source and are unverified against the live service; batch `next`
  pagination is not followed; some metadata keys (`canonical`, `publishedTime`, ...)
  are assumed and simply absent if not returned.
- Official verification is name/domain/text heuristics; the third-party domain list
  is small. Extraction is English-centric and pattern-based, so it will miss facts
  phrased unusually; "mentions technology" is not "uses technology".
- Provider-side SSRF/redirect behaviour and DNS rebinding cannot be fully controlled
  (see `security-and-ssrf.md`). robots.txt handling is best-effort.
- Sites share one DB session (writes serialized); no cross-job caching; no auth or
  rate limiting on the API.

## Manual steps
1. Keep `DATABASE_URL=postgresql+asyncpg://...`, add `SERPAPI_API_KEY` and
   `FIRECRAWL_API_KEY` to `.env`.
2. `docker compose up -d postgres`, then `alembic upgrade head` (applies 0001 and
   0002 without touching existing rows).
3. Run the PostgreSQL integration tests and the live tests (`docs/testing-guide.md`),
   then try `POST /api/v1/research/websites` with `target_names` and review the
   results before the all-in-one `/intelligence` call.

## Findings from the first live run (diagnosed offline, fixes mocked-tested only)
- SerpApi `source` is a publisher name; `SearchResult.domain` now comes from the
  destination URL host and the name is kept as `metadata.publisher`. Google
  properties (`google.com`, `play.google.com`, ...) and app-store hosts are
  third-party unless the requested name is that apex domain.
- Injected HTTP clients ignored the adapters' timeouts (HTTPX's 5 s default
  applied); every request now passes the configured timeout. `/v1/map` uses
  `WEBSITE_RESEARCH_MAP_TIMEOUT_SECONDS` (default 90).
- Live verification of map, scrape, batch, the end-to-end run and PostgreSQL
  persistence of results is **still pending**.

## Live verification (2026-10-31, minimal budget, zero retries)
Target: "Basecamp". Requests actually sent: **1 SerpApi search; Firecrawl 1 map, 1
single scrape, 1 batch creation (2 pages) + 5 status polls.** Pages scraped: 3 (1 + 2).
The end-to-end run used the responses recorded in the same process (no extra provider
calls) and persisted to the disposable `aurora_live_test` PostgreSQL database.
Credits consumed: **not exposed** by either provider's responses.

| Check | Result |
| --- | --- |
| SerpApi search + parsing | **Passed after a fix.** Real responses give `link` as a relative Google redirect (`/goto?url=...`); the destination exists only in `displayed_link`. The parser now uses the displayed host (homepage only, flagged `destination_source=displayed_link`) and never treats Google redirects as destinations. This was the root cause of the `google.com` candidate. |
| Official-site discovery | `basecamp.com` ranked first and `verified`; look-alike brand names (hotels, pub, fitness, Garmin) were not verified. |
| Firecrawl map | Passed (50 URLs, 49 crawl-eligible; classified into 8 page types). |
| Single scrape | Passed (homepage, 24 144 Markdown characters). |
| Batch scrape (2 pages) | Passed (homepage + `/pricing`, 5 polls). |
| End-to-end + PostgreSQL | Passed on replayed live responses: 1 job, 50 mapped pages, 2 documents, 4 entities, 6 claims, 1 report; 0 claims without source URL or excerpt. |
| Timeout behaviour, provider errors | **Not exercised live** (covered by mocked tests only). |
| Quality of extracted facts | Row counts and traceability checked; fact correctness was not manually reviewed. |

PostgreSQL integration tests: 5 passed against the disposable `aurora_test_final`
database (the test module now refuses any other target).
