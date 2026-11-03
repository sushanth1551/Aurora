<#
.SYNOPSIS
  Registers (or removes) the Windows scheduled task that runs the SEO monitor every 14 days.

.DESCRIPTION
  The task runs scripts\seo-monitor.ps1 -Site <site> -StartDatabase as the current user, only
  while you are logged on (the Search Console token is protected with your Windows account).
  If the computer was off at the scheduled time, it runs as soon as possible afterwards.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\register-seo-monitor.ps1
  powershell -ExecutionPolicy Bypass -File scripts\register-seo-monitor.ps1 -EveryDays 14 -At 09:30
  powershell -ExecutionPolicy Bypass -File scripts\register-seo-monitor.ps1 -Unregister
#>
param(
    [string]$Site = 'nextsteps',
    [ValidateRange(1, 90)] [int]$EveryDays = 14,
    [string]$At = '10:00',
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
