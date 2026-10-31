# Module 4 checklist — AI research and intelligence synthesis engine

**Date:** 2026-10-31. Items are ticked only when verified by an executed check.
"Mocked" means verified with fake clients/providers only; **no live LLM request was made.**

## Milestones
- [x] 4.1 Provider abstraction (`llm/base.py`, `models.py`, `exceptions.py`, `provider_factory.py`)
- [x] 4.1 OpenAI provider, Responses API (**mocked only; `openai` package not installed**)
- [x] 4.1 Anthropic provider, Messages API (mocked only; parameters checked against installed SDK 0.125.0)
- [x] 4.1 Mock provider (`llm/mock_provider.py`)
- [x] 4.2 Usage, cost and budgets (`llm/usage_tracker.py`, table `llm_usage_records`)
- [x] 4.3 Context builder (`intelligence/context_builder.py`)
- [x] 4.4 Bounded reasoning engine + controlled follow-up (`reasoning_engine.py`, `followup.py`)
- [x] 4.5 Multi-source synthesis (`synthesis_engine.py`, `confidence.py`)
- [x] 4.6 Reports: JSON + Markdown; PDF behind an exporter interface, not implemented (`report_generator.py`)
- [x] 4.7 Quality evaluation and gates (`quality_evaluator.py`)
- [x] 4.8 API: `POST /intelligence/analyze`, `GET /intelligence/{job_id}`, `/report`, `/usage`, `/quality`
- [x] 4.9 Persistence: 8 tables, migration `0003`, `AnalysisRepository`
- [x] 4.10 Controlled provider fallback (mocked)
- [x] 4.11 Prompt-injection and security controls (see below)
- [x] 4.12 Tests (unit + SQLite integration + PostgreSQL + opt-in live)
- [x] 4.14 Groq provider (`llm/groq_provider.py`, Chat Completions over HTTPX; mocked tests only)
- [x] 4.13 Versioned prompts (`intelligence/prompts/`)

## Acceptance criteria
| # | Criterion | Status |
| --- | --- | --- |
| 1 | Modules 1-3 still work | Verified (all earlier tests pass) |
| 2 | Common provider interface | Verified |
| 3 | OpenAI works with credentials | **Mocked only**; live **not run** (package not installed) |
| 4 | Anthropic works with credentials | **Mocked only**; live **not run** |
| 5 | Mock provider without credentials | Verified (default provider; app starts with no keys) |
| 6 | Configurable provider selection | Verified |
| 7 | Structured output validated with Pydantic | Verified (invalid JSON, schema failures, truncation, repair) |
| 8 | Token usage tracked | Verified (missing usage stays `null`) |
| 9 | Cost from configurable pricing | Verified (no built-in prices; unavailable when unpriced) |
| 10 | Context uses Module 2 + 3 data | Verified (seeded through the real Module 3 pipeline) |
| 11 | Duplicate evidence handled | Verified |
| 12 | Verified / unverified / conflicting separated | Verified |
| 13 | Context limits | Verified (token and source caps) |
| 14 | Research questions generated | Verified (mocked LLM; deterministic defaults on failure) |
| 15 | Missing information detected | Verified (deterministic + LLM) |
| 16 | Contradictions identified | Verified (deterministic + validated LLM) |
| 17 | Follow-ups generated within limits | Verified |
| 18 | Bounded loop terminates | Verified (every stop condition tested) |
| 19 | Follow-ups use existing services | Verified (`WebsiteIntelligenceService.run` over Module 3 fakes) |
| 20 | Multi-source synthesis | Verified |
| 21 | Company and product comparisons | Verified |
| 22 | Findings keep evidence | Verified |
| 23 | JSON and Markdown reports | Verified |
| 24 | Citations validated | Verified (missing, wrong job, missing source, altered excerpt) |
| 25 | Unsupported claims flagged | Verified (no citation, weak support, unsupported figures) |
| 26 | Quality metrics calculated | Verified |
| 27 | API endpoints work | Verified (ASGI client + SQLite; OpenAPI lists 5 paths) |
| 28 | Analysis persisted in PostgreSQL | Verified on a disposable PostgreSQL 16 database (`aurora_test_m4`, dropped afterwards) |
| 29 | Usage records persisted | Verified (SQLite and PostgreSQL) |
| 30 | Migrations work | Verified offline and on PostgreSQL (upgrade, additive, downgrade keeps Module 2/3 rows) |
| 31 | Normal tests spend no credits | Verified (mock/fake only; live tests skipped) |
| 32 | Integration tests use isolated data | Verified (in-memory SQLite; disposable PostgreSQL) |
| 33 | Security implemented and documented | Verified for the listed controls; residual risks below |
| 34 | Regression tests pass | Verified |

## Validation results (executed)
- `ruff check backend scripts`: clean. `ruff format --check`: 169 files formatted.
- `mypy`: no issues in 125 source files. `compileall`: OK.
- `pytest` (final, after the Groq-only finalisation): **620 passed, 13 skipped, 0 failed**.
  Skipped: 3 live LLM (OpenAI, Anthropic, Groq), 3 live SerpApi/Firecrawl, 7 PostgreSQL.
  The PostgreSQL ones ran separately, see below.
- `pip check`: no broken requirements.
- PostgreSQL (`-m postgres`): **7 passed** (5 earlier + 2 Module 4), run twice on disposable
  databases created for the run and dropped afterwards (`aurora_test_m4`, then
  `aurora_test_m4b` after the finalisation changes). The development database was not touched.
- Frontend `npm run build`: succeeded (Vite, 32 modules; the frontend is unchanged).
- `alembic heads` → `0003`; offline SQL matches every ORM table, column, index and constraint.
- `.env.example` and the existing `.env` both load. A blank `INTELLIGENCE_MAX_ESTIMATED_COST=`
  originally crashed settings; this was fixed so blank optional values mean "unset".
- Not run: live OpenAI/Anthropic requests, frontend build (frontend unchanged).

## Decisions that differ from the brief
- **Synchronous analysis** bounded by `INTELLIGENCE_MAX_DURATION_SECONDS` (+30 s hard
  cancel), with progress committed per stage. No background queue exists, and none is
  claimed.
- **Default provider is `mock`** so the app starts without keys. Mock reports are always
  `requires_review` and carry a warning.
- **Hybrid synthesis:** comparison tables come deterministically from claims; the LLM
  adds only the analytical layer, so an over-budget run still yields a cited partial report.
- **Follow-up research** requires both a request opt-in and the server setting, because it
  spends SerpApi/Firecrawl credits.
- **Model override** is allowed only for the configured model or one with a configured
  price.
- **No response caching** across analyses. The context is built once per iteration and
  not re-queried.
- Tests live in the existing flat `backend/tests/` layout rather than `tests/unit|integration`.

## Security controls
Delimited and escaped evidence, with a fixed system prompt treating it as data. The model
cannot supply URLs, tools or code, and follow-up queries are validated. All LLM output is
schema-validated and every cited id is checked. Requests cannot carry system prompts or
raise limits (`extra="forbid"`). SDK error messages are dropped. Prompts, responses and
source text are not stored or logged. Markdown output is escaped. There is no publishing
or external write. Budgets are fail-closed when a cost cap has no price.

**Residual risks:** prompt injection cannot be eliminated. A page can still bias wording
inside allowed fields, which the citation checks only partly catch. Lexical support
checks can pass a statement that cites the right words but misstates their meaning. There
is no API authentication or rate limiting (unchanged from Module 3), so anyone who can
reach the API can spend LLM budget per request up to the configured caps. Cost estimates
depend on manually maintained prices. Provider-side behaviour (retention, model changes)
is outside AURORA's control; OpenAI requests set `store=False`.

## Groq addendum (2026-10-31)
- Model `openai/gpt-oss-20b` (default `GROQ_MODEL`), endpoint `https://api.groq.com/openai/v1`.
- Chose Chat Completions, not the OpenAI Responses adapter: Groq's Responses API is beta.
- Verified against Groq's documentation (not live): JSON-schema structured outputs for
  gpt-oss models (best-effort mode), `include_reasoning`, `usage.prompt_tokens_details.cached_tokens`,
  429 + `retry-after`, prices $0.075 / $0.037 cached / $0.30 per 1M tokens.
- Mocked tests: `tests/test_groq_provider.py` (76 Groq/LLM tests incl. the citation validation
  run with Groq-shaped replies). Live: `tests/test_llm_live.py` (skipped unless enabled).
- **Not verified:** a live Groq call; free-tier limits; whether reasoning tokens are included
  in `completion_tokens`; how a rejected (HTTP 400) generation is billed.
- No database schema change; no migration needed.

## Manual steps
1. Approve and run `pip install openai` in `.venv` (it is in `requirements.txt` but not installed).
2. In `.env`: `LLM_PROVIDER`, the matching `*_API_KEY` and `*_MODEL`, and optionally
   `LLM_PRICING_JSON` with prices from the official pricing pages.
3. `alembic upgrade head` on your database (applies `0003`; additive).
4. Optional live check: `AURORA_RUN_LIVE_LLM_TESTS=true python -m pytest backend/tests/test_llm_live.py -v`
   (one small request per configured provider).

## Groq-only finalisation (2026-10-31)
Groq is the only provider the local setup needs (`LLM_PROVIDER=groq`, `GROQ_MODEL=openai/gpt-oss-20b`,
`GROQ_BASE_URL=https://api.groq.com/openai/v1`); OpenAI and Anthropic adapters remain optional.

**Changes in this pass**
- Per-provider evidence budgets (`INTELLIGENCE_CONTEXT_MAX_TOKENS_<PROVIDER>`, Groq 4 000,
  others inherit the global 12 000), fallback takes the smaller budget, follow-up rebuilds keep
  the run's budget (previously they silently fell back to the global value), and the effective
  budget / trimming are recorded on the analysis and in the report.
- Bugs found by the new evaluation harness and fixed: ranking claims proposed in the reasoning
  step reached `key_findings` as facts (the rule now applies in both layers); pricing claims
  (`subject_type=pricing`) never appeared in comparison tables; `founded_year` and
  `company_size` were not comparison dimensions.
- `scripts/evaluate_module4.py` + `docs/module-4-evaluation.md` (12 objectives, offline mode;
  live mode available but not run).

**Blocked, not done**
- Live Groq test and real Groq analysis: `GROQ_API_KEY` is not set in `.env` (checked for
  presence only, never printed) and `LLM_PROVIDER` is still `mock`. The development database
  also holds 0 research jobs, so there is no stored research to analyse. Set the key, run a
  Module 3 job, then follow `docs/testing-guide.md`.

**Status classification**
| Feature | Status |
| --- | --- |
| Provider abstraction, mock provider, fallback wrapper | implemented, mocked-test verified |
| Groq adapter (Chat Completions, JSON schema / JSON object, reasoning off, usage, errors, retries) | implemented, mocked-test verified; **not live verified** |
| OpenAI adapter (Responses API) | implemented, mocked-test verified; `openai` package not installed; not live verified |
| Anthropic adapter (Messages API) | implemented, mocked-test verified; not live verified |
| Groq pricing (0.075 / 0.037 cached / 0.30 USD per 1M) | configured from Groq's model page; **unverified** against an invoice; Free-plan terms unverified |
| Token usage tracking (missing fields stay null) | implemented, mocked-test verified |
| Cost estimation and budgets | implemented, mocked-test verified |
| Per-provider context budgets, trimming metrics | implemented, mocked-test verified |
| Context builder, reasoning loop, follow-up safety, synthesis, reports, quality gates | implemented, mocked-test verified (fixture data seeded through the real Module 3 pipeline) |
| Citation / evidence-id validation against malformed model output | implemented, mocked-test verified (planted faults caught in all 12 evaluation objectives) |
| PostgreSQL persistence and migration 0003 | live verified on disposable PostgreSQL 16 databases |
| API endpoints | mocked-test verified (ASGI client + SQLite); live server smoke not re-run this pass |
| Report factual correctness, numerical accuracy, readability | **unverified** (manual review not performed; mock output is not real analysis) |
| Frontend build | verified (unchanged code) |
