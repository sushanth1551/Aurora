# Development setup

## Requirements
- Python 3.12.
- Node.js 18+ and npm for the frontend.
- Docker Desktop with Compose v2 for local PostgreSQL (and optionally Neo4j).

## One-command start on Windows

First-time setup, from the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env                # only if .env does not exist yet
.\scripts\init-local-secrets.ps1           # creates AURORA_API_TOKEN and a strong NEO4J_PASSWORD
```

`init-local-secrets.ps1` does the following:
- Asks before changing anything.
- Backs up `.env` to `.env.backup-<timestamp>`, leaving `.env.bak` untouched.
- Keeps existing strong values and never prints a secret.
- If a Neo4j database already exists, changes its password in place with
  `ALTER CURRENT USER`, keeping the data. It writes `.env` only after that succeeds.
- If the existing database does not use the compose example password, pass the current one:

```powershell
.\scripts\init-local-secrets.ps1 -CurrentNeo4jPassword (Read-Host -AsSecureString)
```

**Every start:** double-click `start-aurora.bat` or run `.\scripts\start-aurora.ps1`. It does the
following:
1. Checks Docker, `.venv`, Node/npm, `frontend/node_modules`, `.env` and the API token. If
   `node_modules` is missing, it offers `npm ci`.
2. Runs `docker compose up -d postgres`, plus `neo4j` when a strong `NEO4J_PASSWORD` is set, and
   waits until the containers are healthy. Containers and volumes are never removed.
3. Compares the database with the latest migration. If it is behind, it lists the pending
   migrations and asks before doing anything. On "yes", it first writes a `pg_dump` backup to
   `data\backups\aurora-<timestamp>.dump`, then runs `alembic upgrade head`. Migrations never run
   automatically.
4. Starts the API on `127.0.0.1:8000` and the dashboard on `127.0.0.1:5173` in the background
   (logs in `data\logs`). It waits for both, prints the `/api/v1/health/ready` report and opens
   the browser.

Running the start script again while AURORA is up only reports that it is running. If port 8000
or 5173 is used by another program, it stops with the process id.

Options:
- `-NoBrowser`
- `-NoGraph`: don't start Neo4j.
- `-CopyApiToken`: copy the token to the clipboard for the dashboard prompt.

**Stop:** `stop-aurora.bat` or `.\scripts\stop-aurora.ps1`. It stops only the processes it started
(matched by PID and start time), then runs `docker compose stop`; all data is kept. Use
`-KeepServices` to leave the databases running.

**Dashboard login:** the dashboard asks for the API token once per browser tab. Paste
`AURORA_API_TOKEN` from `.env`. This is the local API token, not a user account (see
`api-documentation.md`, *Authentication*).

**Restoring a backup** taken by the start script (destructive for the target database; do it only
on purpose):

```powershell
docker compose cp data\backups\aurora-<timestamp>.dump postgres:/tmp/restore.dump
docker compose exec -T postgres pg_restore -U aurora -d aurora --clean --if-exists /tmp/restore.dump
```

## Manual start (any platform)

### Backend
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
alembic upgrade head                 # create research tables (back up first; see above)
uvicorn app.main:app --reload --host 127.0.0.1
```

The API *starts* without provider keys or a database. Every route except the
health checks needs `AURORA_API_TOKEN` (503 while it is unset); `POST /api/v1/research`
also needs the provider keys and the database and returns `503` otherwise. `DATABASE_URL` must use the async driver
(`postgresql+asyncpg://...`). Health is at
`http://127.0.0.1:8000/api/v1/health`; Swagger UI is at
`http://127.0.0.1:8000/docs`.

### Frontend
In a second terminal:

```powershell
Set-Location frontend
npm install
Copy-Item .env.example .env
npm run dev
```

The Vite frontend defaults to the local API at `http://127.0.0.1:8000`.
Override `VITE_API_BASE_URL` in `frontend/.env` if needed.

### Local data services
All compose ports are bound to `127.0.0.1` (PostgreSQL 5432, Neo4j 7474/7687, the
optional API container 8000), so nothing is reachable from the network.
`docker compose up -d postgres` starts only PostgreSQL; Neo4j starts only with
`docker compose --profile graph up -d neo4j` and needs a strong `NEO4J_PASSWORD`
(`scripts\init-local-secrets.ps1`). The `api` container image has not been built or
run; the start script runs the API from `.venv` instead. Never use
`docker compose down -v`: it deletes the data volumes.

## Tests and checks
Run `python -m pytest`, `ruff check backend scripts`, and `ruff format --check
backend scripts` from the root. Run `npm run build` in `frontend/`.

## Secrets
Use `.env` for server secrets, `frontend/.env` only for public `VITE_` values,
and `.claude/settings.local.json` for local Claude Code settings. These files
are ignored by Git. Never put private credentials in frontend variables.
