<#
.SYNOPSIS
  Runs the weekly SEO alerts for one website and notifies you only when something needs attention.

.DESCRIPTION
  1. Optionally starts the PostgreSQL container (it holds the sealed Search Console token).
     Containers and volumes are never removed.
  2. Runs scripts\seo_insights.py alerts <site> --pdf: robust drops in clicks and impressions
     (site and pages), query position drops, and URL Inspection of the key pages.
  3. When there are alerts, a PDF "SEO Alerts <date>.pdf" is saved in the site's scheduler
     PDF folder and opened; otherwise only the log and a quiet notification are written.
  4. Logs to data\logs\seo-alerts-<timestamp>.log.

  Read-only: no paid API, nothing is written to the website, Search Console or git.
  Registered weekly by scripts\register-seo-alerts.ps1.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\seo-alerts.ps1 -Site nextsteps -StartDatabase
#>
param(
    [string]$Site = 'nextsteps',
    [switch]$StartDatabase,
    [switch]$NoNotify
)

. (Join-Path $PSScriptRoot 'aurora-common.ps1')

New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null
$log = Join-Path $script:LogDir ('seo-alerts-{0:yyyyMMdd-HHmmss}.log' -f (Get-Date))
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
Write-Log "SEO alerts for '$Site' started."

if ($StartDatabase) {
    if (Test-Command 'docker') {
        Start-DockerEngine -Log { param($m) Write-Log $m } | Out-Null
        $up = Invoke-Compose 'up -d postgres' -TimeoutSeconds 600
        if ($up.ExitCode -ne 0) {
            Write-Log "docker compose up failed (is Docker Desktop running?): $($up.Error.Trim())"
        } elseif (-not (Wait-ContainerHealthy 'postgres' 120)) {
            Write-Log 'PostgreSQL did not become healthy within 120 s; alerts may be blocked.'
        } else {
            Write-Log 'PostgreSQL is healthy.'
        }
    } else {
        Write-Log 'Docker not found; alerts may be blocked.'
    }
}

$run = Invoke-Native -FilePath $script:Python -Arguments "scripts\seo_insights.py alerts $Site --pdf" -TimeoutSeconds 1200
Write-Log "seo_insights.py alerts exit code $($run.ExitCode)"
if ($run.Output) { Add-Content -Path $log -Value $run.Output -Encoding UTF8 }
if ($run.Error) { Add-Content -Path $log -Value $run.Error -Encoding UTF8 }

$result = $null
try { $result = $run.Output | ConvertFrom-Json } catch { }
if ($run.ExitCode -eq 0 -and $result) {
    $note = "$($result.summary) Status: $($result.status)."
    if ($result.blockers.Count -gt 0) { $note = "$note Blockers: $($result.blockers -join '; ')" }
    Write-Log $note
    if ($result.pdf) {
        Show-Notification "AURORA SEO alerts: $Site" "$note PDF: $($result.pdf)"
        Start-Process -FilePath $result.pdf
    } elseif ($result.blockers.Count -gt 0) {
        Show-Notification "AURORA SEO alerts: $Site" $note
    }
    exit 0
}
Write-Log 'The alerts run failed.'
Show-Notification "AURORA SEO alerts: $Site" "The run failed; see $log"
exit 1
