<#
.SYNOPSIS
  Registers (or removes) the Windows scheduled task that runs the weekly SEO alerts.

.DESCRIPTION
  The task runs scripts\seo-alerts.ps1 -Site <site> -StartDatabase as the current user, only
  while you are logged on (the Search Console token is protected with your Windows account).
  If the computer was off at the scheduled time, it runs as soon as possible afterwards.
  Time and interval default to the "alerts" section of
  seo-intelligence\config\sites\<site>.json (default: 09:00 every 7 days).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\register-seo-alerts.ps1
  powershell -ExecutionPolicy Bypass -File scripts\register-seo-alerts.ps1 -EveryDays 7 -At 09:00
  powershell -ExecutionPolicy Bypass -File scripts\register-seo-alerts.ps1 -Unregister
#>
param(
    [string]$Site = 'nextsteps',
    [ValidateRange(0, 30)] [int]$EveryDays = 0,
    [string]$At = '',
    [switch]$Unregister
)

$ErrorActionPreference = 'Stop'
if ($Site -notmatch '^[a-z0-9]+(-[a-z0-9]+)*$') { throw "Invalid site id '$Site'." }
$taskName = "AURORA SEO Alerts ($Site)"

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
$configPath = Join-Path $root "seo-intelligence\config\sites\$Site.json"
if (Test-Path $configPath) {
    $alerts = (Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json).alerts
    if ($alerts) {
        if (-not $At -and $alerts.run_time) { $At = $alerts.run_time }
        if (-not $EveryDays -and $alerts.every_days) { $EveryDays = [int]$alerts.every_days }
    }
}
if (-not $At) { $At = '09:00' }
if (-not $EveryDays) { $EveryDays = 7 }
if ($At -notmatch '^([01]\d|2[0-3]):[0-5]\d$') { throw "Invalid time '$At' (use HH:mm)." }
$runner = Join-Path $PSScriptRoot 'seo-alerts.ps1'
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -Site $Site -StartDatabase"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -DaysInterval $EveryDays -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$description = "AURORA SEO Intelligence: every $EveryDays days, check Search Console for real drops " +
    "and key-page index status for '$Site'. A PDF opens only when there are alerts."

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description $description -Force | Out-Null
$task = Get-ScheduledTask -TaskName $taskName
$next = ($task | Get-ScheduledTaskInfo).NextRunTime
Write-Host "Registered '$taskName': every $EveryDays days at $At. Next run: $next"
Write-Host "Run it now:  Start-ScheduledTask -TaskName '$taskName'"
Write-Host "Remove it:   powershell -ExecutionPolicy Bypass -File scripts\register-seo-alerts.ps1 -Unregister"
