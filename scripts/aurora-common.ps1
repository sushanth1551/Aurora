# Shared helpers for the AURORA Windows scripts (dot-sourced; Windows PowerShell 5.1+).
# Nothing here prints secret values.

$ErrorActionPreference = 'Stop'
$script:Root = Split-Path -Parent $PSScriptRoot
$script:EnvFile = Join-Path $script:Root '.env'
$script:Python = Join-Path $script:Root '.venv\Scripts\python.exe'
$script:RunDir = Join-Path $script:Root 'data\run'
$script:LogDir = Join-Path $script:Root 'data\logs'
$script:BackupDir = Join-Path $script:Root 'data\backups'
$script:PidFile = Join-Path $script:RunDir 'aurora-processes.json'
$script:ExampleNeo4jPassword = 'aurora-local-only'

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "    OK  $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "    !!  $Message" -ForegroundColor Yellow }
function Write-Fail([string]$Message) { Write-Host "    XX  $Message" -ForegroundColor Red }

function Stop-WithError([string]$Message) {
    Write-Fail $Message
    exit 1
}

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# Runs a native program and captures stdout/stderr without PowerShell turning
# stderr lines into errors. Arguments are passed as one pre-quoted string.
function Invoke-Native {
    param(
        [Parameter(Mandatory)] [string]$FilePath,
        [string]$Arguments = '',
        [string]$WorkingDirectory = $script:Root,
        [string]$StandardInput = $null,
        [hashtable]$Environment = @{},
        [int]$TimeoutSeconds = 600
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = $Arguments
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = [bool]$StandardInput
    $psi.CreateNoWindow = $true
    foreach ($key in $Environment.Keys) { $psi.EnvironmentVariables[$key] = [string]$Environment[$key] }
    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if ($StandardInput) {
        $process.StandardInput.WriteLine($StandardInput)
        $process.StandardInput.Close()
    }
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill() } catch { }
        return [pscustomobject]@{ ExitCode = -1; Output = ''; Error = "timed out after $TimeoutSeconds s" }
    }
    $process.WaitForExit()
    return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $stdout.Result; Error = $stderr.Result }
}

function Invoke-Compose([string]$Arguments, [string]$StandardInput = $null, [int]$TimeoutSeconds = 600) {
    return Invoke-Native -FilePath 'docker' -Arguments "compose --profile graph $Arguments" -StandardInput $StandardInput -TimeoutSeconds $TimeoutSeconds
}

# Parses .env like python-dotenv: KEY=VALUE, comments ignored, later lines win.
# Returns @{ Values = hashtable; Duplicates = list of key names }.
function Read-DotEnv([string]$Path = $script:EnvFile) {
    $values = @{}
    $seen = @{}
    $duplicates = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path $Path)) { return @{ Values = $values; Duplicates = $duplicates } }
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        if ($trimmed.StartsWith('export ')) { $trimmed = $trimmed.Substring(7).Trim() }
        $eq = $trimmed.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $trimmed.Substring(0, $eq).Trim()
        $value = $trimmed.Substring($eq + 1).Trim()
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($seen.ContainsKey($key) -and -not $duplicates.Contains($key)) { $duplicates.Add($key) }
        $seen[$key] = $true
        $values[$key] = $value
    }
    return @{ Values = $values; Duplicates = $duplicates }
}

function Get-EnvValue([hashtable]$Values, [string]$Key) {
    if ($Values.ContainsKey($Key)) { return [string]$Values[$Key] }
    return ''
}

function Test-PortListening([int]$Port) {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    return $connection
}

# Status code and parsed JSON body (or $null) for a GET, without throwing on 4xx/5xx.
function Get-HttpJson([string]$Url, [int]$TimeoutSeconds = 5) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds
        $body = $null
        try { $body = $response.Content | ConvertFrom-Json } catch { }
        return [pscustomobject]@{ Status = [int]$response.StatusCode; Body = $body }
    } catch [System.Net.WebException] {
        $webResponse = $_.Exception.Response
        if ($null -eq $webResponse) { return [pscustomobject]@{ Status = 0; Body = $null } }
        # Windows PowerShell 5.1 has already read the error body into ErrorDetails.
        $text = $null
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $text = $_.ErrorDetails.Message }
        else {
            try { $text = (New-Object System.IO.StreamReader($webResponse.GetResponseStream())).ReadToEnd() } catch { }
        }
        $body = $null
        if ($text) { try { $body = $text | ConvertFrom-Json } catch { } }
        return [pscustomobject]@{ Status = [int]$webResponse.StatusCode; Body = $body }
    } catch {
        return [pscustomobject]@{ Status = 0; Body = $null }
    }
}

function Wait-HttpOk([string]$Url, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $result = Get-HttpJson $Url 3
        if ($result.Status -ge 200 -and $result.Status -lt 300) { return $true }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

function Get-ContainerHealth([string]$Service) {
    $ps = Invoke-Compose "ps -q $Service"
    $id = ($ps.Output -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
    if (-not $id) { return 'missing' }
    $inspect = Invoke-Native -FilePath 'docker' -Arguments "inspect --format `"{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}`" $($id.Trim())"
    return $inspect.Output.Trim()
}

function Wait-ContainerHealthy([string]$Service, [int]$TimeoutSeconds = 120) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $health = Get-ContainerHealth $Service
        if ($health -eq 'healthy') { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Read-ProcessFile {
    if (-not (Test-Path $script:PidFile)) { return $null }
    try { return Get-Content $script:PidFile -Raw | ConvertFrom-Json } catch { return $null }
}

# A recorded process is "ours" only if the PID still exists and started at the recorded time
# (PIDs are reused by Windows).
function Get-RecordedProcess($Entry) {
    if ($null -eq $Entry) { return $null }
    $process = Get-Process -Id $Entry.pid -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    try {
        if ($process.StartTime.ToUniversalTime().ToString('o') -ne $Entry.started) { return $null }
    } catch { return $null }
    return $process
}

function Confirm-Choice([string]$Question) {
    $answer = Read-Host "$Question [y/N]"
    return ($answer -match '^(y|yes)$')
}

function New-RandomSecret([int]$Length) {
    $alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'.ToCharArray()
    $bytes = New-Object byte[] ($Length * 2)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $chars = New-Object System.Collections.Generic.List[char]
    try {
        while ($chars.Count -lt $Length) {
            $rng.GetBytes($bytes)
            foreach ($b in $bytes) {
                # Reject bytes that would bias the distribution (256 is not a multiple of 62).
                if ($b -lt 248 -and $chars.Count -lt $Length) { $chars.Add($alphabet[$b % 62]) }
            }
        }
    } finally { $rng.Dispose() }
    return -join $chars
}

function ConvertFrom-Secure([System.Security.SecureString]$Secure) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

# Replace every "KEY=..." line, or append "KEY=value"; preserves line endings, writes UTF-8 without BOM.
function Set-EnvLines([string]$Text, [hashtable]$Updates) {
    $newline = if ($Text.Contains("`r`n")) { "`r`n" } else { "`n" }
    $lines = New-Object System.Collections.Generic.List[string]
    if ($Text.Length -gt 0) { $lines.AddRange([string[]]($Text -split "`r?`n")) }
    if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq '') { $lines.RemoveAt($lines.Count - 1) }
    foreach ($key in $Updates.Keys) {
        $pattern = '^\s*(export\s+)?' + [regex]::Escape($key) + '\s*='
        $found = $false
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match $pattern) { $lines[$i] = "$key=$($Updates[$key])"; $found = $true }
        }
        if (-not $found) { $lines.Add("$key=$($Updates[$key])") }
    }
    return ($lines -join $newline) + $newline
}
