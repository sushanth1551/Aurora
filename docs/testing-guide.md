# Testing guide

All commands run from the repository root with the project virtual environment
active. Default runs use **no real credentials, no network and no database**.

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest                      # unit + API + SQLite repository tests
ruff check backend scripts
ruff format --check backend scripts
mypy                                  # type check (configured in pyproject.toml)
```

## Layout

| File | Covers |
| --- | --- |
| `test_research_schemas.py` | Request validation, enums, state |
| `test_serpapi_tool.py` | Parsing, errors, retry/backoff, Retry-After, key redaction (respx) |
| `test_firecrawl_tool.py` | Success, empty/missing metadata, invalid, 429, 401, timeout, 5xx, size limits, bounded concurrency (respx) |
| `test_source_manager.py` | URL normalization, SSRF rejections, DNS, redirects, dedup, provenance |
| `test_research_service.py` | Pipeline, status rules, partial failure, deadline, cancellation, idempotency |
| `test_research_api.py` | Endpoints with dependency overrides |
| `test_research_repository.py` | Persistence against in-memory SQLite (aiosqlite) |
| `test_research_integration.py` | PostgreSQL + Alembic (opt-in) and live providers (opt-in) |
| `test_health.py`, `test_config.py`, `test_skeletons.py` | Module 1 behaviour |

Fakes (`tests/fakes.py`) replace providers, DNS and (for service/API tests)
persistence. `conftest.py` ignores the real `.env`.

## PostgreSQL integration tests

> **Destructive:** these tests run `alembic downgrade base` on the target database
> (dropping every application table). The test module therefore refuses to run
> unless the URL points at a **local** database named `aurora_test_<suffix>`
> (for example `aurora_test_final`) - never `aurora`, `aurora_test` or
> `aurora_live_test`. A guard test covers this rule.

These apply the Alembic migration to a **disposable** database, run the pipeline
with mocked providers, then downgrade (dropping the tables). Use a dedicated
database, never your development data.

```powershell
docker compose up -d postgres
docker compose exec postgres createdb -U aurora aurora_test_final
$env:AURORA_TEST_DATABASE_URL = "postgresql+asyncpg://aurora:aurora_dev_password@localhost:5432/aurora_test_final"
python -m pytest backend/tests/test_research_integration.py -m postgres -v
```

Without `AURORA_TEST_DATABASE_URL` they are reported as skipped (not passed).

## Optional live tests

Live provider calls spend credits and are never part of the default run. For a
manual check use a hard budget: one search, one map, one scrape, one batch of at
most two pages, a few polls, **zero retries** (set `RESEARCH_MAX_RETRIES=0` and
`WEBSITE_RESEARCH_MAX_RETRIES=0`), and `WEBSITE_RESEARCH_MAX_PROVIDER_REQUESTS`
as a per-run request cap (retries included). A batch of two pages is two pages of
scraping work. Credit consumption is not returned in response headers, so check the
provider dashboards before and after. Save raw SerpApi output only under the
git-ignored `data/raw/` (it contains no key but is third-party content).

Disabled unless `AURORA_RUN_LIVE_INTEGRATION_TESTS=true`. They read keys from the
root `.env`, call SerpApi and Firecrawl for real and **spend credits**.

```powershell
$env:AURORA_RUN_LIVE_INTEGRATION_TESTS = "true"
python -m pytest backend/tests/test_research_integration.py -m live -v
```

## Notes

* Async tests use `pytest-asyncio` in auto mode; HTTP is mocked with `respx`.
* Backoff sleeps are injected, so retry tests run instantly.

## Module 3 tests

| File | Covers |
| --- | --- |
| `test_website_discovery.py` | Query generation, official-site verification, look-alikes, dedup, URL rejection, country/language |
| `test_website_mapper.py` | Classification, exclusion rules, scoring, scope, depth, SSRF on mapped links |
| `test_intelligent_crawler.py` | Selection, limits, partial failure, batch -> individual fallback, retry, revalidation, robots, audit |
| `test_content_processor.py` | Normalization, boilerplate/nav removal, duplicates, truncation, sections, canonical/dates |
| `test_structured_extractor.py` | Company/product/pricing/technology/customer extraction, missing data, LLM validation, prompt injection |
| `test_entity_resolver.py` | Normalization, merging, cross-site separation, stable ids, claim linking |
| `test_evidence_manager.py` | Verification statuses, conflicts, coverage, pricing gaps |
| `test_research_report.py` | JSON/Markdown reports, "not publicly found", no fabrication |
| `test_website_intelligence_service.py` | End-to-end pipeline over fakes + SQLite: partial failure, deadline, cancellation, staged flow |
| `test_website_intelligence_api.py` | Endpoints, validation, pagination, errors (ASGI client) |
| `test_module3_repository.py` | Persistence, constraints, cascades, Module 2 compatibility |
| `test_migrations_offline.py` | Alembic SQL vs. ORM models; additive upgrade; Module 3-only downgrade |

`tests/intel_support.py` provides the fakes (search, map, crawl) and sample pages;
the SQLite `db_session` fixture lives there and is exported by `conftest.py`.
PostgreSQL migration and pipeline tests and a live Firecrawl test are in
`test_research_integration.py` (opt-in, same switches as Module 2).
