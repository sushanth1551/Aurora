<#
.SYNOPSIS
  Starts AURORA locally: PostgreSQL (+ Neo4j when configured), the API and the dashboard.

.DESCRIPTION
  1. Checks prerequisites (Docker running, .venv, Node/npm, frontend packages, .env, API token).
  2. Starts the database containers with docker compose (never removes containers or volumes).
  3. Compares the database with the latest migration and, if behind, asks before backing up the
     database and applying migrations (never automatic).
  4. Starts the API on 127.0.0.1:8000 and the dashboard on 127.0.0.1:5173 in the background,
     logging to data\logs, and waits until both answer.
  5. Prints the readiness report and the dashboard URL.
  Running it again while AURORA is up only reports the running state.
  Stop everything with scripts\stop-aurora.ps1 (or stop-aurora.bat).

.PARAMETER NoBrowser   Do not open the dashboard in the browser.
.PARAMETER NoGraph     Do not start Neo4j even when NEO4J_PASSWORD is set.
.PARAMETER CopyApiToken Copy AURORA_API_TOKEN to the clipboard for the dashboard prompt.
#>
param(
    [switch]$NoBrowser,
    [switch]$NoGraph,
    [switch]$CopyApiToken
)

. (Join-Path $PSScriptRoot 'aurora-common.ps1')

$ApiPort = 8000
$WebPort = 5173
$ApiUrl = "http://127.0.0.1:$ApiPort"
$WebUrl = "http://127.0.0.1:$WebPort"

# -- 0. Already running? ---------------------------------------------------------------
$recorded = Read-ProcessFile
if ($recorded) {
    $api = Get-RecordedProcess $recorded.backend
    $web = Get-RecordedProcess $recorded.frontend
    if ($api -and $web) {
        Write-Ok "AURORA is already running: $WebUrl (API $ApiUrl). Use stop-aurora to stop it."
        exit 0
    }
    if ($api -or $web) {
        Stop-WithError 'AURORA is partly running. Run scripts\stop-aurora.ps1 first, then start again.'
    }
}

# -- 1. Prerequisites ------------------------------------------------------------------
Write-Step 'Checking prerequisites'
if (-not (Test-Command 'docker')) { Stop-WithError 'Docker is not installed or not on PATH. Install Docker Desktop.' }
$info = Invoke-Native -FilePath 'docker' -Arguments 'info --format "{{.ServerVersion}}"' -TimeoutSeconds 30
if ($info.ExitCode -ne 0) { Stop-WithError 'Docker is installed but not running. Start Docker Desktop and try again.' }
Write-Ok "Docker $($info.Output.Trim())"
$composeVersion = Invoke-Native -FilePath 'docker' -Arguments 'compose version --short'
if ($composeVersion.ExitCode -ne 0) { Stop-WithError 'docker compose (v2) is not available.' }

if (-not (Test-Path $script:Python)) {
    Stop-WithError '.venv not found. Create it: py -3.12 -m venv .venv; .venv\Scripts\pip install -r requirements.txt; .venv\Scripts\pip install -e ".[dev]"'
}
Write-Ok 'Python virtual environment'
if (-not (Test-Command 'npm')) { Stop-WithError 'Node.js/npm is not installed or not on PATH (Node 20+).' }
if (-not (Test-Path (Join-Path $script:Root 'frontend\node_modules'))) {
    Write-Warn 'frontend\node_modules is missing.'
    if (-not (Confirm-Choice 'Install the frontend packages now with "npm ci" (from package-lock.json)?')) {
        Stop-WithError 'Frontend packages are required. Run: cd frontend; npm ci'
    }
    $install = Invoke-Native -FilePath 'cmd.exe' -Arguments '/c npm ci' -WorkingDirectory (Join-Path $script:Root 'frontend') -TimeoutSeconds 900
    if ($install.ExitCode -ne 0) { Stop-WithError "npm ci failed: $($install.Error.Trim())" }
}
Write-Ok 'Node.js and frontend packages'

if (-not (Test-Path $script:EnvFile)) {
    Stop-WithError '.env not found. Run: Copy-Item .env.example .env; scripts\init-local-secrets.ps1'
}
$parsed = Read-DotEnv
$values = $parsed.Values
if ($parsed.Duplicates.Count -gt 0) {
    Write-Warn "Keys defined more than once in .env (the last one wins): $($parsed.Duplicates -join ', ')"
}
if ((Get-EnvValue $values 'AURORA_API_TOKEN').Length -lt 32) {
    Stop-WithError 'AURORA_API_TOKEN is missing or too short. Run: scripts\init-local-secrets.ps1'
}
Write-Ok '.env with AURORA_API_TOKEN'
$neo4jPassword = Get-EnvValue $values 'NEO4J_PASSWORD'
$withGraph = (-not $NoGraph) -and $neo4jPassword
if ($withGraph -and ($neo4jPassword -eq $script:ExampleNeo4jPassword -or $neo4jPassword.Length -lt 16)) {
    Write-Warn 'NEO4J_PASSWORD is the example or too short; Neo4j is not started. Run scripts\init-local-secrets.ps1.'
    $withGraph = $false
}
if (-not $withGraph -and -not $NoGraph -and -not $neo4jPassword) {
    Write-Warn 'NEO4J_PASSWORD is not set: the knowledge graph stays disabled (optional).'
}

foreach ($port in @($ApiPort, $WebPort)) {
    $listener = Test-PortListening $port
    if ($listener) {
        $owner = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
        Stop-WithError "Port $port is already in use by PID $($listener.OwningProcess) ($($owner.ProcessName)). Stop it or free the port."
    }
}

# -- 2. Database containers ----------------------------------------------------------------
Write-Step 'Starting database containers'
$services = 'postgres'
if ($withGraph) { $services = 'postgres neo4j' }
$up = Invoke-Compose "up -d $services" -TimeoutSeconds 600
if ($up.ExitCode -ne 0) { Stop-WithError "docker compose up failed: $($up.Error.Trim())" }
if (-not (Wait-ContainerHealthy 'postgres' 120)) { Stop-WithError 'PostgreSQL did not become healthy within 120 s (docker compose logs postgres).' }
Write-Ok 'PostgreSQL healthy'
if ($withGraph) {
    if (Wait-ContainerHealthy 'neo4j' 180) { Write-Ok 'Neo4j healthy' }
    else { Write-Warn 'Neo4j is not healthy yet; the graph will report unavailable (docker compose logs neo4j).' }
}

# -- 3. Migrations (never automatic) ----------------------------------------------------
Write-Step 'Checking database migrations'
$alembicEnv = @{ PYTHONPATH = (Join-Path $script:Root 'backend') }
$current = Invoke-Native -FilePath $script:Python -Arguments '-m alembic current' -Environment $alembicEnv -TimeoutSeconds 60
$heads = Invoke-Native -FilePath $script:Python -Arguments '-m alembic heads' -Environment $alembicEnv -TimeoutSeconds 60
if ($current.ExitCode -ne 0 -or $heads.ExitCode -ne 0) {
    Write-Warn 'Could not read the migration state (alembic). The API will report it in /health/ready.'
} else {
    $currentRev = ([regex]::Match($current.Output, '(?m)^\s*([0-9A-Za-z_]+)')).Groups[1].Value
    $headRev = ([regex]::Match($heads.Output, '(?m)^\s*([0-9A-Za-z_]+)')).Groups[1].Value
    if ($currentRev -and $currentRev -eq $headRev) {
        Write-Ok "Database at the latest migration ($headRev)"
    } else {
        $from = if ($currentRev) { $currentRev } else { 'none (empty database)' }
        $range = if ($currentRev) { "${currentRev}:head" } else { 'base:head' }
        $history = Invoke-Native -FilePath $script:Python -Arguments "-m alembic history -r $range" -Environment $alembicEnv
        Write-Warn "Database is at $from; latest is $headRev. Pending migrations:"
        ($history.Output -split "`r?`n") | Where-Object { $_.Trim() -and $_ -notmatch "^$currentRev\b" } | ForEach-Object { Write-Host "        $_" }
        Write-Host '    A backup (pg_dump custom format) is written to data\backups before anything changes.'
        if (Confirm-Choice '    Back up the database and apply the pending migrations now?') {
            New-Item -ItemType Directory -Force -Path $script:BackupDir | Out-Null
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            $target = Join-Path $script:BackupDir "aurora-$stamp.dump"
            $dump = Invoke-Compose 'exec -T postgres pg_dump -U aurora -d aurora -Fc -f /tmp/aurora-backup.dump'
            if ($dump.ExitCode -ne 0) { Stop-WithError "Backup failed; no migration was applied: $($dump.Error.Trim())" }
            $copy = Invoke-Compose "cp postgres:/tmp/aurora-backup.dump `"$target`""
            Invoke-Compose 'exec -T postgres rm -f /tmp/aurora-backup.dump' | Out-Null
            if ($copy.ExitCode -ne 0 -or -not (Test-Path $target) -or (Get-Item $target).Length -eq 0) {
                Stop-WithError 'Backup could not be copied out of the container; no migration was applied.'
            }
            Write-Ok "Backup: data\backups\aurora-$stamp.dump ($([math]::Round((Get-Item $target).Length / 1KB)) KB)"
            $upgrade = Invoke-Native -FilePath $script:Python -Arguments '-m alembic upgrade head' -Environment $alembicEnv -TimeoutSeconds 600
            if ($upgrade.ExitCode -ne 0) {
                Stop-WithError "Migration failed (restore with pg_restore from the backup if needed): $($upgrade.Error.Trim())"
            }
            Write-Ok "Migrated to $headRev"
        } else {
            Write-Warn 'Migrations not applied. Features that need the newer tables will fail until you migrate.'
        }
    }
}

# -- 4. API and dashboard ------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $script:RunDir, $script:LogDir | Out-Null
Write-Step "Starting the API on $ApiUrl"
$backend = Start-Process -FilePath $script:Python -PassThru -WindowStyle Hidden `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', "$ApiPort" `
    -WorkingDirectory $script:Root `
    -RedirectStandardOutput (Join-Path $script:LogDir 'backend.out.log') `
    -RedirectStandardError (Join-Path $script:LogDir 'backend.err.log')

Write-Step "Starting the dashboard on $WebUrl"
$frontend = Start-Process -FilePath 'cmd.exe' -PassThru -WindowStyle Hidden `
    -ArgumentList '/c', 'npm', 'run', 'dev', '--', '--strictPort' `
    -WorkingDirectory (Join-Path $script:Root 'frontend') `
    -RedirectStandardOutput (Join-Path $script:LogDir 'frontend.out.log') `
    -RedirectStandardError (Join-Path $script:LogDir 'frontend.err.log')

@{
    backend  = @{ pid = $backend.Id; started = $backend.StartTime.ToUniversalTime().ToString('o') }
    frontend = @{ pid = $frontend.Id; started = $frontend.StartTime.ToUniversalTime().ToString('o') }
    startedAt = (Get-Date).ToString('o')
} | ConvertTo-Json | Set-Content -Path $script:PidFile -Encoding ASCII

if (-not (Wait-HttpOk "$ApiUrl/api/v1/health" 60)) {
    Stop-WithError 'The API did not start within 60 s. See data\logs\backend.err.log, then run stop-aurora.'
}
Write-Ok 'API is up'
if (-not (Wait-HttpOk $WebUrl 90)) {
    Stop-WithError 'The dashboard did not start within 90 s. See data\logs\frontend.err.log, then run stop-aurora.'
}
Write-Ok 'Dashboard is up'

# -- 5. Health report ------------------------------------------------------------------------
Write-Step 'Readiness'
$ready = Get-HttpJson "$ApiUrl/api/v1/health/ready" 15
if ($ready.Body) {
    $r = $ready.Body
    $line = "    overall: $($r.status) | database: $($r.database.status) | migrations: $($r.migrations.status)"
    if ($r.migrations.pending -and $r.migrations.pending.Count -gt 0) { $line += " (pending: $($r.migrations.pending -join ', '))" }
    $line += " | graph: $($r.graph.status)"
    $color = if ($r.status -eq 'ready') { 'Green' } else { 'Yellow' }
    Write-Host $line -ForegroundColor $color
} else {
    Write-Warn "Readiness check failed (HTTP $($ready.Status))."
}

if ($CopyApiToken) {
    Set-Clipboard -Value (Get-EnvValue $values 'AURORA_API_TOKEN')
    Write-Ok 'AURORA_API_TOKEN copied to the clipboard; paste it into the dashboard.'
}
Write-Host ''
Write-Host "AURORA is running:  $WebUrl" -ForegroundColor Green
Write-Host "API docs:           $ApiUrl/docs"
Write-Host 'Dashboard token:    AURORA_API_TOKEN from .env (use -CopyApiToken to copy it)'
Write-Host 'Logs:               data\logs   |   Stop: stop-aurora.bat or scripts\stop-aurora.ps1'
if (-not $NoBrowser) { Start-Process $WebUrl }
