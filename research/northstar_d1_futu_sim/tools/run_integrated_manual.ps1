param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('HK', 'US')]
    [string]$Market
)

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$python = 'C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe'
$root = 'E:\quant'
$runtime = 'E:\quant\research\northstar_d1_futu_sim\runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$log = Join-Path $runtime ("integrated_manual_{0}.log" -f $Market.ToLowerInvariant())
Set-Location -LiteralPath $root

"[$([DateTimeOffset]::Now.ToString('o'))] START manual-validation market=$Market" |
    Add-Content -LiteralPath $log -Encoding UTF8
& $python -m research.northstar_d1_futu_sim.forward_runner `
    --market $Market --manual-validation 2>&1 |
    Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE
"[$([DateTimeOffset]::Now.ToString('o'))] END manual-validation market=$Market exit=$code" |
    Add-Content -LiteralPath $log -Encoding UTF8
exit $code
