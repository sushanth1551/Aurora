<#
.SYNOPSIS
  Runs the scheduled SEO monitor for one website and notifies you when the report is ready.

.DESCRIPTION
  1. Optionally starts the PostgreSQL container (it holds the sealed Search Console token).
     Containers and volumes are never removed.
  2. Runs scripts\seo_monitor.py <site>: tracks your repository changes (read-only git),
     updates the change checklist, checks live titles and Search Console rankings, and writes
     seo-intelligence\sites\<site>\reports\<execution>.md (also latest-report.md).
  3. Logs to data\logs\seo-monitor-<timestamp>.log and shows a Windows notification.

  No paid API is called and nothing is written to the website, Search Console or git.
  Registered every 14 days by scripts\register-seo-monitor.ps1.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\seo-monitor.ps1 -Site nextsteps -StartDatabase -OpenReport
#>
param(
    [string]$Site = 'nextsteps',
    [switch]$StartDatabase,
    [switch]$OpenReport,
    [switch]$NoNotify
)

. (Join-Path $PSScriptRoot 'aurora-common.ps1')

New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null
$log = Join-Path $script:LogDir ('seo-monitor-{0:yyyyMMdd-HHmmss}.log' -f (Get-Date))
function Write-Log([string]$Message) {
    Add-Content -Path $log -Value ('{0:s} {1}' -f (Get-Date), $Message) -Encoding UTF8
}

function Show-Notification([string]$Title, [string]$Text) {
    if ($NoNotify) { return }
    try {
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        $template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
        $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)
        $nodes = $xml.GetElementsByTagName('text')
        $nodes.Item(0).AppendChild($xml.CreateTextNode($Title)) | Out-Null
        $nodes.Item(1).AppendChild($xml.CreateTextNode($Text)) | Out-Null
        $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
        $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
    } catch {
        Write-Log "Notification not shown: $($_.Exception.Message)"
    }
}

if ($Site -notmatch '^[a-z0-9]+(-[a-z0-9]+)*$') { Stop-WithError "Invalid site id '$Site'." }
if (-not (Test-Path $script:Python)) { Stop-WithError "Python virtualenv not found at $script:Python." }
Write-Log "SEO monitor for '$Site' started."

if ($StartDatabase) {
    if (Test-Command 'docker') {
        $up = Invoke-Compose 'up -d postgres' -TimeoutSeconds 600
        if ($up.ExitCode -ne 0) {
            Write-Log "docker compose up failed (is Docker Desktop running?): $($up.Error.Trim())"
        } elseif (-not (Wait-ContainerHealthy 'postgres' 120)) {
            Write-Log 'PostgreSQL did not become healthy within 120 s; rankings may be skipped.'
        } else {
            Write-Log 'PostgreSQL is healthy.'
        }
    } else {
        Write-Log 'Docker not found; rankings may be skipped.'
    }
}

$run = Invoke-Native -FilePath $script:Python -Arguments "scripts\seo_monitor.py $Site" -TimeoutSeconds 1800
Write-Log "seo_monitor.py exit code $($run.ExitCode)"
if ($run.Output) { Add-Content -Path $log -Value $run.Output -Encoding UTF8 }
if ($run.Error) { Add-Content -Path $log -Value $run.Error -Encoding UTF8 }

$result = $null
try { $result = $run.Output | ConvertFrom-Json } catch { }
if ($run.ExitCode -eq 0 -and $result -and $result.report) {
    $note = "Status: $($result.status). Report: seo-intelligence\sites\$Site\reports\latest-report.md"
    if ($result.blockers.Count -gt 0) { $note = "$note (blockers: $($result.blockers -join '; '))" }
    Write-Log $note
    Show-Notification "AURORA SEO monitor: $Site" $note
    if ($OpenReport) { Start-Process -FilePath $result.report }
    exit 0
}
Write-Log 'The monitor did not produce a report.'
Show-Notification "AURORA SEO monitor: $Site" "The run failed; see $log"
exit 1
