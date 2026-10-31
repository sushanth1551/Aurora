# Development setup

## Requirements
- Python 3.12.
- Node.js 18+ and npm for the frontend.
- Docker Compose v2 only when using local PostgreSQL/Neo4j.

## Backend on Windows PowerShell
From the project root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"   # pytest, respx, aiosqlite, mypy, ruff
Copy-Item .env.example .env          # root .env; add SERPAPI_API_KEY and FIRECRAWL_API_KEY
docker compose up -d postgres        # PostgreSQL only
$env:PYTHONPATH = "backend"
alembic upgrade head                 # create research tables
uvicorn app.main:app --reload
```

The API *starts* without provider keys or a database; `POST /api/v1/research`
needs both and returns `503` otherwise. `DATABASE_URL` must use the async driver
(`postgresql+asyncpg://...`). Health is at
`http://127.0.0.1:8000/api/v1/health`; Swagger UI is at
`http://127.0.0.1:8000/docs`.

## Frontend
In a second terminal:

```powershell
Set-Location frontend
npm install
Copy-Item .env.example .env
npm run dev
```

The Vite frontend defaults to the local API at `http://127.0.0.1:8000`.
Override `VITE_API_BASE_URL` in `frontend/.env` if needed.

## Local data services
Copy `.env.example` to `.env`, set a local-only `NEO4J_PASSWORD`, then start
services from the root with `docker compose up --build`. Compose maps PostgreSQL
to `127.0.0.1:5432` and the API to 8000. `docker compose up -d postgres` starts
only PostgreSQL. Neo4j is unused in Module 2 and starts only with
`docker compose --profile graph up neo4j`. The compose file was validated with
`docker compose config`; containers were not built or run during development.

## Tests and checks
Run `python -m pytest`, `ruff check backend scripts`, and `ruff format --check
backend scripts` from the root. Run `npm run build` in `frontend/`.

## Secrets
Use `.env` for server secrets, `frontend/.env` only for public `VITE_` values,
and `.claude/settings.local.json` for local Claude Code settings. These files
are ignored by Git. Never put private credentials in frontend variables.
