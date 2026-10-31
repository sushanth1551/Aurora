<#
.SYNOPSIS
  Creates the local secrets AURORA needs: AURORA_API_TOKEN and a strong NEO4J_PASSWORD.

.DESCRIPTION
  - Only adds or replaces these two keys in .env; every other line is kept as is.
  - Backs up .env to .env.backup-<timestamp> before writing (never touches .env.bak).
  - Keeps existing values that are already strong; never prints any secret.
  - If a Neo4j database already exists (container or data volume), its password is changed
    in place with ALTER CURRENT USER (data is kept); .env is written only after that succeeds.
  - Asks for confirmation before changing anything.

.PARAMETER CopyApiToken
  Copy AURORA_API_TOKEN to the clipboard at the end (for pasting into the dashboard).

.PARAMETER CurrentNeo4jPassword
  The password the existing Neo4j database uses now, if it is not the value in .env or the
  compose default. You are prompted for it securely when needed.
#>
param(
    [switch]$CopyApiToken,
    [System.Security.SecureString]$CurrentNeo4jPassword
)

. (Join-Path $PSScriptRoot 'aurora-common.ps1')

Write-Step 'Checking .env'
if (-not (Test-Path $script:EnvFile)) {
    Stop-WithError ".env not found. Create it first: Copy-Item .env.example .env  (then re-run this script)."
}
$parsed = Read-DotEnv
$values = $parsed.Values
$updates = @{}

$apiToken = Get-EnvValue $values 'AURORA_API_TOKEN'
if ($apiToken.Length -ge 32) {
    Write-Ok 'AURORA_API_TOKEN is already set (kept).'
} else {
    $apiToken = New-RandomSecret 48
    $updates['AURORA_API_TOKEN'] = $apiToken
    Write-Ok 'AURORA_API_TOKEN will be created (48 random characters).'
}

$neo4jUser = Get-EnvValue $values 'NEO4J_USERNAME'
if (-not $neo4jUser) { $neo4jUser = 'neo4j' }
$currentNeo4j = Get-EnvValue $values 'NEO4J_PASSWORD'
$rotateFrom = $null
if ($currentNeo4j.Length -ge 16 -and $currentNeo4j -ne $script:ExampleNeo4jPassword) {
    Write-Ok 'NEO4J_PASSWORD is already set to a strong value (kept).'
} else {
    $newNeo4j = New-RandomSecret 32
    $updates['NEO4J_PASSWORD'] = $newNeo4j
    Write-Ok 'NEO4J_PASSWORD will be set to a new 32-character random value.'

    # Does a Neo4j database already exist? Then its stored password must be changed too.
    $project = (Split-Path -Leaf $script:Root).ToLower()
    $volume = Invoke-Native -FilePath 'docker' -Arguments "volume inspect ${project}_neo4j_data"
    $container = Invoke-Compose 'ps -a -q neo4j'
    if ($volume.ExitCode -eq 0 -or $container.Output.Trim()) {
        if ($CurrentNeo4jPassword) { $rotateFrom = ConvertFrom-Secure $CurrentNeo4jPassword }
        elseif ($currentNeo4j) { $rotateFrom = $currentNeo4j }
        else { $rotateFrom = $script:ExampleNeo4jPassword }  # compose default used at first start
        Write-Warn 'An existing Neo4j database was found; its password will be changed in place (data kept).'
    }
}

if ($updates.Count -eq 0) {
    Write-Ok 'Nothing to change.'
} else {
    Write-Host ''
    Write-Host "Keys to write in .env: $($updates.Keys -join ', ')  (values are not shown)"
    if (-not (Confirm-Choice 'Back up .env and apply these changes?')) { Stop-WithError 'Cancelled; nothing was changed.' }

    if ($rotateFrom) {
        Write-Step 'Changing the password of the existing Neo4j database'
        if (-not (Test-Command 'docker')) { Stop-WithError 'Docker is required to change the Neo4j password.' }
        $up = Invoke-Compose 'up -d neo4j'
        if ($up.ExitCode -ne 0) { Stop-WithError "Could not start Neo4j: $($up.Error.Trim())" }
        if (-not (Wait-ContainerHealthy 'neo4j' 180)) { Stop-WithError 'Neo4j did not become healthy; nothing was changed.' }
        # Old password on the command line (it is being retired); the new one only via stdin.
        $statement = "ALTER CURRENT USER SET PASSWORD FROM '$rotateFrom' TO '$($updates['NEO4J_PASSWORD'])';"
        $alter = Invoke-Compose "exec -T neo4j cypher-shell -u $neo4jUser -p `"$rotateFrom`" -d system" $statement 120
        if ($alter.ExitCode -ne 0) {
            Stop-WithError ("Neo4j rejected the password change (exit $($alter.ExitCode)). .env was not modified. " +
                'If the database uses another password, re-run with -CurrentNeo4jPassword (Read-Host -AsSecureString).')
        }
        Write-Ok 'Neo4j password changed.'
    }

    $backup = "$($script:EnvFile).backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $script:EnvFile -Destination $backup
    Write-Ok "Backup written: $(Split-Path -Leaf $backup) (git-ignored)."
    $text = [System.IO.File]::ReadAllText($script:EnvFile)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($script:EnvFile, (Set-EnvLines $text $updates), $utf8NoBom)
    Write-Ok ".env updated: $($updates.Keys -join ', ')."
    if ($rotateFrom) {
        Write-Warn 'The next start recreates the Neo4j container with the new password (the data volume is kept).'
    }
}

if ($parsed.Duplicates.Count -gt 0) {
    Write-Warn "Keys defined more than once in .env (the last one wins): $($parsed.Duplicates -join ', ')"
}
if ($CopyApiToken) {
    Set-Clipboard -Value $apiToken
    Write-Ok 'AURORA_API_TOKEN copied to the clipboard.'
}
Write-Host ''
Write-Host 'Done. Start AURORA with: .\start-aurora.bat   (or scripts\start-aurora.ps1)'
