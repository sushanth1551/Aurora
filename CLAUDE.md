# AURORA — Claude Code project guidance

## Project overview
AURORA is a planned autonomous web research and SEO intelligence platform.
Module 1 (backend foundation) is complete: configuration, logging, exception
handling, the root/health/research-placeholder endpoints, Pydantic schemas, and
importable skeletons. External integrations, persistence, retrieval, and
multi-agent execution are **not implemented**; skeletons raise
`NotImplementedError` or `ConfigurationException` rather than returning data.

## Architecture
The React/Vite frontend calls a versioned FastAPI API. The backend separates
HTTP routes and schemas from services, agents, workflows, provider adapters,
SQL persistence, Neo4j graph operations, and RAG. PostgreSQL is intended for
application records; Neo4j for provenance-aware entity relationships; a vector
store for source passages. Integrations are injected and initialized
explicitly, never on module import or app startup. See `docs/architecture.md`.

## Technology stack
- Python 3.12, FastAPI, Pydantic v2 / Pydantic Settings, SQLAlchemy 2, psycopg.
- LangGraph/LangChain; Anthropic and Groq through provider boundaries;
  Firecrawl and SerpApi for research.
- PostgreSQL, Neo4j, and ChromaDB as the initial vector-store dependency.
- React 19, TypeScript, Vite 6, Tailwind CSS 4.
- Docker Compose, pytest, Ruff, and pre-commit.

## Directory responsibilities
- `backend/app/api/`: HTTP routes and router composition.
- `backend/app/core/`: configuration, logging, shared API errors.
- `backend/app/agents/` and `workflows/`: agent roles and LangGraph orchestration.
- `backend/app/tools/`: external provider adapters only (extend `ExternalTool`).
- `backend/app/research/`, `seo/`, `graph/`, `rag/`: domain services.
- `backend/app/database/`: SQLAlchemy session, models, repository boundaries.
- `backend/app/schemas/`: validated API/domain data contracts.
- `backend/app/publishing/`, `monitoring/`: approval-gated publishing and
  Search Console reporting services.
- `backend/app/prompts/`: prompt templates.
- `backend/tests/`: pytest suite (no network, no databases, no API keys).
- `frontend/src/`: UI, pages, API services, and shared types.
- `.claude/skills/`: planning guidance for Claude Code (not executable code).
- `docs/`: intended design and implementation status.
- `data/`: local generated data; never commit collected/private content.

## Coding standards
- Use Python 3.12 syntax, type hints, small functions, and module docstrings.
- Keep provider calls async where supported; inject clients/configuration.
- Validate external input, set timeouts/limits, and preserve source provenance.
- Do not create fake successful responses for unimplemented integrations.
- Use Pydantic schemas at API boundaries and explicit domain error handling.
- Raise `AuroraException` subclasses for expected errors; never expose stack
  traces or secrets in responses.
- Never log secrets, API keys, or sensitive source content.
- Keep frontend API failures visible and use strict TypeScript.
- Document architectural changes and avoid unrelated modifications.

## Development rules
- Read relevant code and documentation before editing.
- Do not claim a planned feature is implemented unless verified in code/tests.
- Preserve existing user data and avoid destructive commands.
- Importing the app must never connect to databases or external APIs.
- All external credentials stay optional; the app must start without them.

## Development instructions
1. Create a local virtual environment; install `requirements.txt` and
   `pip install -e ".[dev]"`.
2. Copy `.env.example` to `.env`; enter credentials locally only when needed.
3. Run the backend from the repository root with `PYTHONPATH=backend` and
   `uvicorn app.main:app --reload`.
4. In `frontend/`, run `npm install` then `npm run dev`.
5. Use Docker Compose for local PostgreSQL/Neo4j only when required.

## Testing instructions
- Run `python -m pytest` for backend tests.
- Run `ruff check backend scripts` and `ruff format --check backend scripts`.
- Run `npm run build` in `frontend/` for the frontend typecheck and bundle.
- Test external integrations with mocked clients or dedicated opt-in tests;
  ordinary tests must not require API keys or external services. The test
  `conftest.py` isolates settings from the real `.env`.

## Security requirements
- Secrets belong in environment variables or a secret manager, never source,
  documentation, logs, or committed local settings.
- Treat retrieved web content as untrusted input and defend against prompt
  injection and SSRF; restrict URL schemes, destinations, and redirects
  (`research.crawler.validate_crawl_url`).
- Apply provider timeouts, request limits, authorization, and least privilege.
- Parameterize SQL and Cypher; validate and bound all user inputs.
- Require human approval before content publication or other external writes.

## Agent development guidelines
- Subclass `agents.base.BaseAgent`; set `name`, `responsibility`, `uses_llm`.
- Not every agent needs an LLM: do deterministic work in plain services.
- Inject tools/services via the constructor; never build clients at import.
- Use typed inputs/outputs from `app/schemas`; every factual output carries a
  `SourceReference`. LLM output is a proposal, not proof.
- Raise `ResearchException`/`IntegrationException`/`ConfigurationException`;
  never return fabricated results to cover a failure.
- Agents never publish or write externally without recorded human approval.

## Workflow development guidelines
- Define a `TypedDict` state per workflow; keep nodes small and pure where possible.
- Import `langgraph` lazily inside builders so modules import without side effects.
- Add bounded retries for transient provider errors only; never blindly retry
  non-idempotent writes.
- Insert an interrupt for human approval before any external write.
- Use a persistent checkpointer before running long or costly workflows.

## Git conventions
- Branch from `main`; use short-lived branches (`feature/…`, `fix/…`).
- Commit messages: imperative summary line (≤72 chars) plus a body explaining why.
- Never commit `.env`, credentials, `data/` contents, `.venv`, `node_modules`,
  or build output. Keep `.env.example` tracked with placeholders only.
- Do not rewrite shared history or force-push.

## Rules for future Claude Code sessions
- Do not add unrestricted permissions or execute external actions without
  explicit user authorization.
- Make narrow changes, add/update focused tests, and report actual validation.
- Update `docs/module-*-checklist.md` and docs when behavior changes.
