<#
.SYNOPSIS
  Nightly market-data update for trade2 (Windows PowerShell 5.1+ / PowerShell 7).

.DESCRIPTION
  Ingests universe/tsx_composite.csv and universe/sp500.csv with the default incremental policy
  (prices + whatever is due), then runs `valuation value`, which writes reports/<date>/.
  Runs `valuation ingest` and `valuation value` only. It never runs a backtest command.
  Setup, scheduling and troubleshooting: docs/ops/nightly_update.md

  Environment (all optional):
    TRADE2_UV                  path to uv.exe (default: first `uv` on PATH)
    TRADE2_DEEP_REFRESH_DAY    ISO weekday 1-7 (1 = Mon, 6 = Sat) on which to add --refresh-history
    TRADE2_RETRY_DELAY         seconds to wait before retrying a failed step once (default 300)
    TRADE2_LOG_KEEP_DAYS       delete logs\nightly_*.log older than this (default 30)
    TRADE2_NOTIFY_CMD          PowerShell command run on failure; it sees $env:TRADE2_STATUS,
                               $env:TRADE2_FAILED_STEPS and $env:TRADE2_LOG
    VALUATION_SEC_USER_AGENT   passed through to SEC EDGAR ("<app> <contact email>"), never set here

.PARAMETER DryRun
  Print the steps and exit (no lock, no log, no fetch).

.PARAMETER Deep
  Also refetch price history (--refresh-history).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File C:\src\trade2\scripts\nightly_update.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Deep
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

# Repo root = parent of this script's directory; no hard-coded paths.
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $RepoRoot

# Task Scheduler may start with a PATH that lacks the user's uv install dir. Append the usual ones.
foreach ($d in @("$env:USERPROFILE\.local\bin", "$env:USERPROFILE\.cargo\bin",
                 "$env:LOCALAPPDATA\Microsoft\WinGet\Links")) {
    if ($d -and (Test-Path -LiteralPath $d) -and (($env:PATH -split ';') -notcontains $d)) {
        $env:PATH = "$env:PATH;$d"
    }
}

function Get-EnvOr([string]$Name, [string]$Default) {
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrEmpty($v)) { return $Default } else { return $v }
}

$RetryDelay = [int](Get-EnvOr 'TRADE2_RETRY_DELAY' '300')
$KeepDays   = [int](Get-EnvOr 'TRADE2_LOG_KEEP_DAYS' '30')
$Today      = (Get-Date).ToString('yyyy-MM-dd')
$IsoDow     = [int]((Get-Date).DayOfWeek); if ($IsoDow -eq 0) { $IsoDow = 7 }   # Sunday = 7
$DeepDay    = Get-EnvOr 'TRADE2_DEEP_REFRESH_DAY' ''
if ($DeepDay -ne '' -and $DeepDay -eq "$IsoDow") { $Deep = $true }

# Steps. One `value` run covers every ingested ticker (both universes): two runs would write the
# same reports/<date>/ files and the second would overwrite the first.
$Universes = @('universe/tsx_composite.csv', 'universe/sp500.csv')
$Steps = @()
foreach ($u in $Universes) {
    $a = @('ingest', '--universe', $u)
    if ($Deep) { $a += '--refresh-history' }
    $Steps += , @{ Name = 'ingest ' + [IO.Path]::GetFileNameWithoutExtension($u); Args = $a }
}
$Steps += , @{ Name = 'value'; Args = @('value', '--out', 'reports') }

$Uv = Get-EnvOr 'TRADE2_UV' ''
if ($Uv -eq '') {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { $Uv = $cmd.Source }
}
$UvOk = ($Uv -ne '') -and (Test-Path -LiteralPath $Uv)

if ($DryRun) {
    Write-Output "trade2 nightly update (dry run) in $RepoRoot"
    Write-Output ("uv: " + $(if ($UvOk) { $Uv } else { 'NOT FOUND' }))
    Write-Output "report dir: reports/$Today/"
    Write-Output "log: logs/nightly_$Today.log (kept $KeepDays days)"
    Write-Output ("deep refresh: " + $(if ($Deep) { 'yes' } else { 'no' }))
    Write-Output ("VALUATION_SEC_USER_AGENT: " +
        $(if ($env:VALUATION_SEC_USER_AGENT) { 'set' } else { 'not set (EDGAR default)' }))
    $i = 1
    foreach ($s in $Steps) { Write-Output ("step ${i}: uv run valuation " + ($s.Args -join ' ')); $i++ }
    if (-not $UvOk) { Write-Error 'uv not found; see docs/ops/nightly_update.md (PATH)' -ErrorAction Continue; exit 127 }
    exit 0
}

New-Item -ItemType Directory -Force -Path 'logs' | Out-Null
$Log = Join-Path $RepoRoot "logs\nightly_$Today.log"
$LockPath = Join-Path $RepoRoot 'logs\.nightly.lock'

function Write-Log([string]$Msg) {
    $line = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $Msg
    Write-Output $line
    Add-Content -LiteralPath $Log -Value $line -Encoding UTF8
}

# Lock: an exclusively opened file. Windows releases it when the process exits, even on a crash,
# so a stale lock cannot block later runs.
try {
    $Lock = [IO.File]::Open($LockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite,
                            [IO.FileShare]::None)
} catch {
    Write-Log "SKIP: another nightly update is running (lock $LockPath is held)"
    exit 0
}

$Status = 0
$Failed = @()
try {
    $w = New-Object IO.StreamWriter($Lock); $w.Write("$PID"); $w.Flush()
    Write-Log "=== trade2 nightly update start (repo $RepoRoot, pid $PID, deep=$Deep) ==="

    Get-ChildItem -LiteralPath 'logs' -Filter 'nightly_*.log' -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) } |
        Remove-Item -Force -ErrorAction SilentlyContinue

    if (-not $UvOk) {
        Write-Log "ERROR: uv not found on PATH ($env:PATH). Install uv or set TRADE2_UV; see docs/ops/nightly_update.md"
        $Status = 127
        $Failed += 'uv-missing'
    } else {
        Write-Log "uv: $Uv"
        if (-not $env:VALUATION_SEC_USER_AGENT) {
            Write-Log 'note: VALUATION_SEC_USER_AGENT not set; EDGAR gets the default user agent'
        }
        $env:PYTHONUTF8 = '1'
        $env:PYTHONIOENCODING = 'utf-8'
        try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

        $ingestFailed = $false
        foreach ($s in $Steps) {
            if ($s.Name -eq 'value' -and $ingestFailed) {
                Write-Log "skipping 'value': an ingest step failed, so no report for $Today"
                $Failed += 'value (skipped)'
                continue
            }
            $ok = $false
            foreach ($attempt in 1, 2) {
                Write-Log ("--- step '" + $s.Name + "' attempt ${attempt}: uv run valuation " + ($s.Args -join ' ') + ' ---')
                # Native stderr (Python logging) arrives as ErrorRecords; don't let them stop the script.
                $prev = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                & $Uv run valuation @($s.Args) 2>&1 | ForEach-Object { Write-Log ("$_") }
                $rc = $LASTEXITCODE
                $ErrorActionPreference = $prev
                if ($rc -eq 0) { Write-Log ("step '" + $s.Name + "' ok"); $ok = $true; break }
                Write-Log ("step '" + $s.Name + "' failed (exit $rc)")
                if ($attempt -eq 1) {
                    Write-Log "retrying in ${RetryDelay}s (Yahoo throttling / DB lock usually clears)"
                    Start-Sleep -Seconds $RetryDelay
                }
            }
            if (-not $ok) {
                $Status = 1
                $Failed += $s.Name
                if ($s.Name -like 'ingest*') { $ingestFailed = $true }
            }
        }
    }

    if ($Status -eq 0) {
        Write-Log "=== trade2 nightly update OK: reports/$Today/ ==="
    } else {
        $failedText = $Failed -join ', '
        Write-Log "=== trade2 nightly update FAILED (exit $Status): $failedText ==="
        $notify = Get-EnvOr 'TRADE2_NOTIFY_CMD' ''
        if ($notify -ne '') {
            Write-Log 'running TRADE2_NOTIFY_CMD'
            $env:TRADE2_STATUS = "$Status"
            $env:TRADE2_FAILED_STEPS = $failedText
            $env:TRADE2_LOG = $Log
            try {
                $prev = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                Invoke-Expression $notify 2>&1 | ForEach-Object { Write-Log ("$_") }
                $ErrorActionPreference = $prev
            } catch {
                Write-Log "warning: TRADE2_NOTIFY_CMD failed: $_"
            }
        }
    }
} finally {
    $Lock.Dispose()
    Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
}
exit $Status
