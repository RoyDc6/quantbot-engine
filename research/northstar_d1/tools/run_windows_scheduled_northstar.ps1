param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("HK", "US")]
    [string]$Market
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$python = "C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe"
$workingDirectory = "E:\quant"
$logDirectory = "E:\quant\research\northstar_d1\output\scheduler_logs"
$module = "research.northstar_d1.windows_scheduler_$($Market.ToLowerInvariant())"
$logPath = Join-Path $logDirectory "windows_scheduler_$($Market.ToLowerInvariant()).log"

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Set-Location -LiteralPath $workingDirectory

$startedAt = [DateTimeOffset]::Now.ToString("o")
"[$startedAt] START market=$Market module=$module" | Out-File -LiteralPath $logPath -Append -Encoding utf8
& $python -m $module 2>&1 | ForEach-Object {
    $line = $_.ToString()
    Write-Output $line
    $line | Out-File -LiteralPath $logPath -Append -Encoding utf8
}
$exitCode = $LASTEXITCODE
$endedAt = [DateTimeOffset]::Now.ToString("o")
"[$endedAt] END market=$Market exit_code=$exitCode" | Out-File -LiteralPath $logPath -Append -Encoding utf8
exit $exitCode
