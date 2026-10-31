# Module 2 checklist — autonomous web research engine

**Date:** 2026-10-31. Items are ticked only when verified by an executed check.
"Pending" items need credentials or a running PostgreSQL that were not available
during development.

## Milestones
- [x] 2.1 Research state and data contracts (`schemas/research.py`, `research/state.py`)
- [x] 2.2 SerpApi adapter (HTTPX, retries, Retry-After, normalization) — mocked tests
- [x] 2.3 Firecrawl adapter (HTTPX `/v1/scrape`, size limits, bounded batch) — mocked tests
- [x] 2.4 Source manager and URL/SSRF policy (`research/url_policy.py`, `source_manager.py`)
- [x] 2.5 Research service (stages, status rules, deadline, cancellation, idempotency)
- [x] 2.6 FastAPI endpoints (`POST /api/v1/research`, `GET /api/v1/research/{job_id}`)
- [x] 2.7 Tests (mocked unit/API/SQLite tests pass; PostgreSQL and live tests written, **not run**)
- [x] 2.8 PostgreSQL persistence code and Alembic migration (SQL generated offline; **not applied to a live PostgreSQL**)

## Acceptance criteria
| # | Criterion | Status |
| --- | --- | --- |
| 1 | FastAPI backend starts | Verified (uvicorn started with no keys, no database) |
| 2 | Root and health endpoints still work | Verified (tests + live smoke) |
| 3 | Research endpoint validates requests | Verified |
| 4 | SerpApi performs a real search with credentials | **Pending** (no key; mocked tests only) |
| 5 | Results converted to typed models | Verified (mocked) |
| 6 | URLs validated before fetching | Verified |
| 7 | Private/unsafe destinations rejected | Verified (IPv4/IPv6, DNS, redirects) |
| 8 | Firecrawl extracts a public page with credentials | **Pending** (no key; mocked tests only) |
| 9 | Source provenance retained | Verified |
| 10 | Duplicate URLs removed | Verified |
| 11 | Partial extraction failures handled | Verified |
| 12 | Jobs saved in PostgreSQL | Verified on SQLite; **PostgreSQL not run** |
| 13 | Sources linked to their jobs | Verified (FK + cascade on SQLite); PostgreSQL not run |
| 14 | Job retrievable by ID | Verified (SQLite + API) |
| 15 | Migrations work | Offline SQL + model parity verified; **not applied to PostgreSQL** |
| 16 | Unit tests run without credentials | Verified |
| 17 | Integration tests on isolated PostgreSQL | **Not run** (`AURORA_TEST_DATABASE_URL` unset) |
| 18 | Live tests disabled by default | Verified (skipped) |
| 19 | Logs do not expose secrets | Verified (tests assert key absence) |
| 20 | Documentation explains setup/operation | Done |

## Validation results (executed)
- `compileall`: OK. `ruff check`: clean. `ruff format --check`: 97 files formatted.
- `mypy`: no issues in 79 source files. `pip check`: no broken requirements.
- `pytest`: **212 passed, 5 skipped, 0 failed** (217 collected). Skipped: 3
  PostgreSQL integration tests, 2 live-provider tests.
- `alembic heads` → `0001`; `alembic upgrade head --sql` and
  `downgrade 0001:base --sql` generate valid PostgreSQL DDL.
- Smoke test with real uvicorn: `/` 200, `/docs` 200, `POST /research` and
  `GET /research/{id}` return a clean `503 persistence_error` when PostgreSQL is
  not running (no secrets in response or log).
- `docker compose config`: valid. **Containers/images were not built or run.**
- `npm run build`: succeeded.

## Known limitations
- Provider-side SSRF (Firecrawl fetches on its network) and DNS rebinding are not
  fully controllable; see `security-and-ssrf.md`.
- Execution is synchronous with a 60 s default deadline; no background queue.
- Firecrawl/SerpApi response shapes are from the installed SDK and public API,
  unverified against live services.
- Redirect preflight is opt-in; ports limited to 80/443/8080/8443.
- No authentication or rate limiting on the API yet.
- No cross-job cache (content hashes are stored for it).

## Manual steps
1. Set `DATABASE_URL=postgresql+asyncpg://...` in `.env` (yours still uses
   `postgresql+psycopg://`), add `SERPAPI_API_KEY` and `FIRECRAWL_API_KEY`.
2. `docker compose up -d postgres`, then `alembic upgrade head`.
3. Run the PostgreSQL integration tests and the live tests
   (`docs/testing-guide.md`) and report any difference.
