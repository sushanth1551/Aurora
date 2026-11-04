<#
.SYNOPSIS
  Copies AURORA's Claude Code agents (.claude\agents\*.md) to your personal Claude folder.

.DESCRIPTION
  The AURORA project folder is the source of truth for its agents; the copies in
  %USERPROFILE%\.claude\agents\ let you use them from any project. Runs:
  - as a Claude Code PostToolUse hook (.claude\settings.json) after every Edit/Write: it reads
    the hook JSON from stdin and only acts when the edited file is in .claude\agents;
  - from the hourly reindex reminder task, to pick up edits made outside Claude;
  - by hand: powershell -ExecutionPolicy Bypass -File scripts\sync-agents.ps1
  A personal copy that differs is backed up to data\backups\agents\ before it is replaced, so
  an edit made only in the personal folder is never lost. Files are only ever added or
  replaced, never deleted.
#>
param(
    [switch]$Quiet,
    # Sync without reading hook input (scheduled task, manual runs).
    [switch]$All
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root '.claude\agents'
$target = Join-Path $env:USERPROFILE '.claude\agents'

# Hook mode: only react to edits of agent files.
if (-not $All -and [Console]::IsInputRedirected) {
    $raw = [Console]::In.ReadToEnd()
    if ($raw.Trim()) {
        try { $evt = $raw | ConvertFrom-Json } catch { exit 0 }
        $path = [string]$evt.tool_input.file_path
        if ($path -notmatch '[\\/]\.claude[\\/]agents[\\/][^\\/]+\.md$') { exit 0 }
    }
}

if (-not (Test-Path $source)) { exit 0 }
New-Item -ItemType Directory -Force -Path $target | Out-Null
$backupDir = Join-Path $root 'data\backups\agents'
$copied = @()
foreach ($file in Get-ChildItem -Path $source -Filter '*.md' -File) {
    $dest = Join-Path $target $file.Name
    if (Test-Path $dest) {
        if ((Get-FileHash $file.FullName).Hash -eq (Get-FileHash $dest).Hash) { continue }
        New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        Copy-Item $dest (Join-Path $backupDir "$($file.BaseName).personal-$stamp.md")
    }
    Copy-Item $file.FullName $dest -Force
    $copied += $file.Name
}
if ($copied -and -not $Quiet) {
    # Shown to the user by Claude Code when run as a hook.
    $msg = "Synced to your personal Claude folder: " + ($copied -join ', ')
    @{ systemMessage = $msg } | ConvertTo-Json -Compress
}
exit 0
