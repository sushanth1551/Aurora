<#
.SYNOPSIS
  Registers (or removes) the Windows scheduled task that runs the SEO monitor every 14 days.

.DESCRIPTION
  The task runs scripts\seo-monitor.ps1 -Site <site> -StartDatabase as the current user, only
  while you are logged on (the Search Console token is protected with your Windows account).
  If the computer was off at the scheduled time, it runs as soon as possible afterwards.
  Time and interval default to the "scheduler" section of
  seo-intelligence\config\sites\<site>.json (nextsteps: 12:00 every 14 days).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\register-seo-monitor.ps1
  powershell -ExecutionPolicy Bypass -File scripts\register-seo-monitor.ps1 -EveryDays 14 -At 12:00
  powershell -ExecutionPolicy Bypass -File scripts\register-seo-monitor.ps1 -Unregister
#>
param(
    [string]$Site = 'nextsteps',
    [ValidateRange(0, 90)] [int]$EveryDays = 0,
    [string]$At = '',
    [switch]$Unregister
)

$ErrorActionPreference = 'Stop'
if ($Site -notmatch '^[a-z0-9]+(-[a-z0-9]+)*$') { throw "Invalid site id '$Site'." }
$taskName = "AURORA SEO Monitor ($Site)"

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
# Time and interval come from the site config ("scheduler") unless given here.
$configPath = Join-Path $root "seo-intelligence\config\sites\$Site.json"
if (Test-Path $configPath) {
    $scheduler = (Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json).scheduler
    if (-not $At -and $scheduler.run_time) { $At = $scheduler.run_time }
    if (-not $EveryDays -and $scheduler.every_days) { $EveryDays = [int]$scheduler.every_days }
}
if (-not $At) { $At = '12:00' }
if (-not $EveryDays) { $EveryDays = 14 }
if ($At -notmatch '^([01]\d|2[0-3]):[0-5]\d$') { throw "Invalid time '$At' (use HH:mm)." }
$runner = Join-Path $PSScriptRoot 'seo-monitor.ps1'
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -Site $Site -StartDatabase"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -DaysInterval $EveryDays -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$description = "AURORA SEO Intelligence: every $EveryDays days, track changes, update the checklist " +
    "and report blog rankings for '$Site'. Reports: seo-intelligence\sites\$Site\reports."

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description $description -Force | Out-Null
$task = Get-ScheduledTask -TaskName $taskName
$next = ($task | Get-ScheduledTaskInfo).NextRunTime
Write-Host "Registered '$taskName': every $EveryDays days at $At. Next run: $next"
Write-Host "Run it now:  Start-ScheduledTask -TaskName '$taskName'"
Write-Host "Remove it:   powershell -ExecutionPolicy Bypass -File scripts\register-seo-monitor.ps1 -Unregister"
