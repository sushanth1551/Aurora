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
python -m pip install -e ".[dev]"   # pytest, pytest-cov, ruff
Copy-Item .env.example .env          # root-level .env; all keys optional
$env:PYTHONPATH = "backend"
uvicorn app.main:app --reload
```

The API starts without external provider keys or databases. Health is at
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
to port 5432, Neo4j Browser to 7474, Bolt to 7687, and the API to 8000. Database
connections are not yet used by the scaffold API.

## Tests and checks
Run `python -m pytest`, `ruff check backend scripts`, and `ruff format --check
backend scripts` from the root. Run `npm run build` in `frontend/`.

## Secrets
Use `.env` for server secrets, `frontend/.env` only for public `VITE_` values,
and `.claude/settings.local.json` for local Claude Code settings. These files
are ignored by Git. Never put private credentials in frontend variables.
