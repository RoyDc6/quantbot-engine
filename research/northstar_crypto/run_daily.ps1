$ErrorActionPreference = 'Stop'
$quantProject = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$pythonRuntime = 'C:\Users\RoyGoode\AppData\Local\Programs\Python\Python312\python.exe'
Push-Location -LiteralPath $quantProject
try {
    & $pythonRuntime -B -X utf8 -m research.northstar_crypto --demo-execute --compare
    $dailyExit = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $dailyExit
