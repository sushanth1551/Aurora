<#
.SYNOPSIS
  Registers (or removes) the Windows scheduled task for the daily reindex reminder.

.DESCRIPTION
  The task runs scripts\reindex-reminder.ps1 every hour and at logon, as the current user and
  only while you are logged on (the window must appear on your screen). Each run exits at once
  unless a batch is due: 24 hours after the last batch you recorded, or after "Remind me in
  2 hours". When nothing needs reindexing it looks again every 6 hours for new deploys.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\register-reindex-reminder.ps1
  powershell -ExecutionPolicy Bypass -File scripts\register-reindex-reminder.ps1 -Unregister
#>
param(
    [string]$Site = 'nextsteps',
    [switch]$Unregister
)

$ErrorActionPreference = 'Stop'
if ($Site -notmatch '^[a-z0-9]+(-[a-z0-9]+)*$') { throw "Invalid site id '$Site'." }
$taskName = "AURORA Reindex Reminder ($Site)"

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed scheduled task '$taskName'."
    } else {
        Write-Host "No scheduled task named '$taskName'."
    }
    return
}

$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot 'reindex-reminder.ps1'
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -Site $Site"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $root
$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours((Get-Date).Hour + 1) `
    -RepetitionInterval (New-TimeSpan -Hours 1)
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$description = "AURORA: daily reindex reminder for '$Site'. Shows the day's pages to request " +
    "indexing for in Search Console, 24 hours after the last recorded batch. " +
    "Status: seo-intelligence\sites\$Site\reindex\status.md"

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($hourly, $logon) `
    -Settings $settings -Description $description -Force | Out-Null
Write-Host "Registered '$taskName': checks hourly and at logon; shows a window when a batch is due."
Write-Host "Show it now:  powershell -ExecutionPolicy Bypass -File scripts\reindex-reminder.ps1 -Force"
Write-Host "Remove it:    powershell -ExecutionPolicy Bypass -File scripts\register-reindex-reminder.ps1 -Unregister"
