<#
.SYNOPSIS
  Daily reindex reminder: shows the day's pages to request indexing for in Search Console.

.DESCRIPTION
  Run hourly and at logon by the scheduled task from scripts\register-reindex-reminder.ps1.
  1. Asks scripts\reindex_queue.py whether a batch is due (24 hours after the last confirmed
     batch). If not, it exits at once: no network, no Docker.
  2. When due: starts Docker Desktop and PostgreSQL if needed (they hold the Search Console
     token), then refreshes the queue from the live site and Search Console.
  3. Shows a notification and a window with the date, the day's pages, the pages newly added
     since the last batch, and the pages Google re-read since then. Buttons:
       Open in Search Console   opens each page's URL Inspection screen in your browser;
                                you click "Request indexing" on each (Google allows no API).
       I requested them         asks you to confirm, then records the pages as requested
                                with today's date; the next batch is ready 24 hours later.
       Remind me in 2 hours     closes the window and asks again later.
  Nothing is sent to Google or written to the website. Logs: data\logs\reindex-*.log.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\reindex-reminder.ps1 -Site nextsteps
  powershell -ExecutionPolicy Bypass -File scripts\reindex-reminder.ps1 -Force   # show now
#>
param(
    [string]$Site = 'nextsteps',
    [switch]$Force
)

. (Join-Path $PSScriptRoot 'aurora-common.ps1')
if ($Site -notmatch '^[a-z0-9]+(-[a-z0-9]+)*$') { Stop-WithError "Invalid site id '$Site'." }

New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null
$log = Join-Path $script:LogDir ('reindex-{0:yyyyMMdd}.log' -f (Get-Date))
function Write-Log([string]$Message) {
    Add-Content -Path $log -Value ('{0:s} {1}' -f (Get-Date), $Message) -Encoding UTF8
}

function Invoke-Queue([string]$Arguments) {
    $run = Invoke-Native -FilePath $script:Python -Arguments "scripts\reindex_queue.py $Arguments" -TimeoutSeconds 1800
    if ($run.Error) { Write-Log ("stderr: " + $run.Error.Trim()) }
    try { return $run.Output | ConvertFrom-Json } catch {
        Write-Log "reindex_queue.py $Arguments returned no JSON (exit $($run.ExitCode))."
        return $null
    }
}

function Show-Toast([string]$Title, [string]$Text) {
    try {
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
            [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $nodes = $xml.GetElementsByTagName('text')
        $nodes.Item(0).AppendChild($xml.CreateTextNode($Title)) | Out-Null
        $nodes.Item(1).AppendChild($xml.CreateTextNode($Text)) | Out-Null
        $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
            [Windows.UI.Notifications.ToastNotification]::new($xml))
    } catch { Write-Log "Toast not shown: $($_.Exception.Message)" }
}

# 1. Due? (cheap, no network)
if (-not $Force) {
    $check = Invoke-Queue "check $Site"
    if (-not $check) { exit 1 }
    if (-not $check.due) { exit 0 }
} else {
    Invoke-Queue "snooze $Site --hours 0" | Out-Null
}
Write-Log "Batch due for '$Site'."

# 2. Database for the Search Console token, then refresh.
if (Test-Command 'docker') {
    Start-DockerEngine -Log { param($m) Write-Log $m } | Out-Null
    $up = Invoke-Compose 'up -d postgres' -TimeoutSeconds 600
    if ($up.ExitCode -ne 0 -or -not (Wait-ContainerHealthy 'postgres' 120)) {
        Write-Log 'PostgreSQL not available; trying anyway.'
    }
}
$result = Invoke-Queue "refresh $Site"
if (-not $result -or $result.error) {
    $why = if ($result) { $result.message } else { 'no result' }
    Write-Log "Refresh failed: $why"
    Show-Toast 'AURORA reindex reminder' "Could not prepare today's pages ($why). See $log"
    Invoke-Queue "snooze $Site --hours 2" | Out-Null
    exit 1
}
if (-not $result.due -or -not $result.batch) {
    Write-Log 'Nothing to request today: Google has every changed page.'
    exit 0
}
$batch = $result.batch
$count = @($batch.pages).Count
Write-Log "Showing batch $($batch.id) with $count page(s)."
Show-Toast "Reindex today: $count page(s)" "$($batch.date). Open the window to request indexing in Search Console."

# 3. The window.
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("Date: $($batch.date)")
$lines.Add('')
$lines.Add("TODAY'S PAGES ($count): open each one and click 'Request indexing'")
$i = 0
foreach ($p in $batch.pages) {
    $i++
    $isNew = if (@($batch.newly_added) -contains $p.path) { '  [new]' } else { '' }
    $lines.Add(("{0,2}. {1}{2}" -f $i, $p.title, $isNew))
    $lines.Add("    $($p.url)   (changed $($p.changed))")
}
$lines.Add('')
$added = @($batch.newly_added)
$lines.Add("NEWLY ADDED since the last batch: $($added.Count)")
foreach ($a in $added | Select-Object -First 15) { $lines.Add("  + $a") }
if ($added.Count -gt 15) { $lines.Add("  ... and $($added.Count - 15) more") }
$lines.Add('')
$crawled = @($batch.crawled_since_last)
$lines.Add("GOOGLE RE-READ since the last batch (done, dropped from the list): $($crawled.Count)")
foreach ($c in $crawled | Select-Object -First 15) { $lines.Add("  - $c") }
if ($crawled.Count -gt 15) { $lines.Add("  ... and $($crawled.Count - 15) more") }
$lines.Add('')
$lines.Add("Still waiting after today: $($batch.remaining_after) page(s). Next batch: 24 hours after you record this one.")

$form = New-Object System.Windows.Forms.Form
$form.Text = "AURORA: reindex today ($($batch.date))"
$form.Size = New-Object System.Drawing.Size(900, 640)
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$box = New-Object System.Windows.Forms.TextBox
$box.Multiline = $true; $box.ReadOnly = $true; $box.ScrollBars = 'Vertical'
$box.Font = New-Object System.Drawing.Font('Consolas', 10)
$box.Location = New-Object System.Drawing.Point(12, 12)
$box.Size = New-Object System.Drawing.Size(860, 520)
$box.Text = ($lines -join "`r`n")
$form.Controls.Add($box)

$open = New-Object System.Windows.Forms.Button
$open.Text = "Open $count in Search Console"
$open.Location = New-Object System.Drawing.Point(12, 545); $open.Size = New-Object System.Drawing.Size(250, 40)
$open.Add_Click({
    foreach ($p in $batch.pages) { Start-Process $p.inspect; Start-Sleep -Milliseconds 400 }
    Write-Log "Opened $count Search Console tabs."
})
$form.Controls.Add($open)

$done = New-Object System.Windows.Forms.Button
$done.Text = 'I requested them: record'
$done.Location = New-Object System.Drawing.Point(275, 545); $done.Size = New-Object System.Drawing.Size(250, 40)
$done.Add_Click({
    $when = Get-Date -Format 'dd MMM yyyy HH:mm'
    $msg = "Record these $count page(s) as requested on $when?`r`n`r`n" +
        (($batch.pages | ForEach-Object { $_.path }) -join "`r`n") +
        "`r`n`r`nThe next batch will be ready 24 hours from now."
    $answer = [System.Windows.Forms.MessageBox]::Show($form, $msg, 'Confirm reindex requests', 'YesNo', 'Question')
    if ($answer -eq 'Yes') {
        $c = Invoke-Queue "confirm $Site $($batch.id)"
        if ($c -and -not $c.error) {
            Write-Log "Recorded batch $($batch.id) ($count pages)."
            [System.Windows.Forms.MessageBox]::Show($form, "Recorded $count page(s). Next batch in 24 hours.", 'Done') | Out-Null
            $form.Tag = 'done'; $form.Close()
        } else {
            [System.Windows.Forms.MessageBox]::Show($form, "Could not record: $($c.message)", 'Error') | Out-Null
        }
    }
})
$form.Controls.Add($done)

$later = New-Object System.Windows.Forms.Button
$later.Text = 'Remind me in 2 hours'
$later.Location = New-Object System.Drawing.Point(538, 545); $later.Size = New-Object System.Drawing.Size(200, 40)
$later.Add_Click({ $form.Close() })
$form.Controls.Add($later)

[void]$form.ShowDialog()
if ($form.Tag -ne 'done') {
    Invoke-Queue "snooze $Site --hours 2" | Out-Null
    Write-Log 'Snoozed for 2 hours.'
}
exit 0
