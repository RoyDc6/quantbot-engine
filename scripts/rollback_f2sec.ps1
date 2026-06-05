param(
    [Parameter(Mandatory=$true)]
    [string]$CoreCommitHash,

    [string]$ReconciliationCommitHash = "",

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Action
    )
    Write-Host "[F2-SEC rollback] $Name" -ForegroundColor Yellow
    if ($DryRun) {
        Write-Host "  DRY-RUN: skipped"
        return
    }
    & $Action
}

Set-Location (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))

Invoke-Step "Set process and user kill switches" {
    $env:QUANT_LIVE_KILLED = "YES"
    [Environment]::SetEnvironmentVariable("QUANT_LIVE_KILLED", "YES", "User")
    New-Item -ItemType Directory -Force -Path ".\output" | Out-Null
    "F2-SEC rollback at $(Get-Date -Format o)" | Set-Content -Encoding UTF8 ".\output\LIVE_DISABLED_FLAG"
}

Invoke-Step "Stop Futu OpenD" {
    $procs = Get-CimInstance Win32_Process -Filter "Name='Futu_OpenD.exe'"
    foreach ($proc in $procs) {
        Stop-Process -Id $proc.ProcessId -Force
    }
    Start-Sleep -Seconds 1
    $remaining = Get-CimInstance Win32_Process -Filter "Name='Futu_OpenD.exe'"
    if ($remaining) {
        throw "Futu_OpenD.exe still running after stop attempt"
    }
}

Invoke-Step "Stop running unified_runner.py processes" {
    $runners = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*unified_runner.py*" }
    foreach ($runner in $runners) {
        Stop-Process -Id $runner.ProcessId -Force
    }
}

Invoke-Step "Disable scheduled tasks" {
    schtasks /Change /TN "AutoTradeHK" /DISABLE | Out-Null
    schtasks /Change /TN "AutoTradeUS" /DISABLE | Out-Null
}

Invoke-Step "Verify core commit hash exists" {
    git cat-file -t $CoreCommitHash | Out-Null
}

Invoke-Step "Revert core F2-SEC commit" {
    git revert --no-edit $CoreCommitHash
}

if ($ReconciliationCommitHash -ne "") {
    Invoke-Step "Restore reconciliation tool from dedicated commit" {
        git cat-file -t $ReconciliationCommitHash | Out-Null
        git checkout $ReconciliationCommitHash -- core/reconciliation_tool.py
    }
}

Invoke-Step "Verify journal files are preserved" {
    Get-ChildItem ".\output\order_journal_*.db" -ErrorAction SilentlyContinue | Out-Null
    Write-Host "  Journal files were not deleted by this script."
}

Write-Host "[F2-SEC rollback] complete" -ForegroundColor Green
