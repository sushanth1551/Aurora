<#
.SYNOPSIS
  Stops AURORA: the API and dashboard processes started by start-aurora, then the containers.

.DESCRIPTION
  Only processes recorded by start-aurora (matched by PID *and* start time) are stopped.
  Containers are stopped with "docker compose stop": containers, volumes and data are kept.
  Nothing is deleted.

.PARAMETER KeepServices  Leave PostgreSQL/Neo4j running (stop only the API and dashboard).
#>
param([switch]$KeepServices)

. (Join-Path $PSScriptRoot 'aurora-common.ps1')

Write-Step 'Stopping the API and dashboard'
$recorded = Read-ProcessFile
if (-not $recorded) {
    Write-Ok 'No AURORA processes recorded (nothing started by start-aurora).'
} else {
    foreach ($name in @('backend', 'frontend')) {
        $process = Get-RecordedProcess $recorded.$name
        if ($process) {
            # /T stops the child processes too (uvicorn workers, node under npm).
            $kill = Invoke-Native -FilePath 'taskkill.exe' -Arguments "/PID $($process.Id) /T /F"
            if ($kill.ExitCode -eq 0) { Write-Ok "$name stopped (PID $($process.Id))" }
            else { Write-Warn "$name (PID $($process.Id)) could not be stopped: $($kill.Error.Trim())" }
        } else {
            Write-Ok "$name was not running"
        }
    }
    Remove-Item -LiteralPath $script:PidFile -ErrorAction SilentlyContinue
}

foreach ($port in @(8000, 5173)) {
    $listener = Test-PortListening $port
    if ($listener) {
        Write-Warn "Port $port is still in use by PID $($listener.OwningProcess); it was not started by start-aurora, so it was left alone."
    }
}

if ($KeepServices) {
    Write-Ok 'Containers left running (-KeepServices).'
} elseif (-not (Test-Command 'docker')) {
    Write-Warn 'Docker not found; containers were not touched.'
} else {
    Write-Step 'Stopping containers (data is kept)'
    $stop = Invoke-Compose 'stop' -TimeoutSeconds 180
    if ($stop.ExitCode -eq 0) { Write-Ok 'Containers stopped. Start again with start-aurora.' }
    else { Write-Warn "docker compose stop reported a problem: $($stop.Error.Trim())" }
}
