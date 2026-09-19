param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('HK', 'US')]
    [string]$Market
)

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location 'E:\quant'
$runtime = 'E:\quant\research\northstar_d1_futu_sim\runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$log = Join-Path $runtime ("scheduled_{0}.log" -f $Market.ToLowerInvariant())
"[$([DateTime]::Now.ToString('o'))] START market=$Market" | Add-Content -LiteralPath $log -Encoding UTF8
python -m research.northstar_d1_futu_sim.runner --market $Market --execute-sim 2>&1 |
    Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE
"[$([DateTime]::Now.ToString('o'))] END market=$Market exit=$code" | Add-Content -LiteralPath $log -Encoding UTF8
exit $code
