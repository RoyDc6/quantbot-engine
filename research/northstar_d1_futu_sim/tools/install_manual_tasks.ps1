$ErrorActionPreference = 'Stop'

$stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$backupDir = Join-Path 'E:\quant\research\northstar_d1_futu_sim\migration_backups' $stamp
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$taskNames = @(
    'NorthstarD1-Forward-HK-Manual',
    'NorthstarD1-Forward-US-Manual'
)
foreach ($name in $taskNames) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        Export-ScheduledTask -TaskName $name |
            Set-Content -LiteralPath (Join-Path $backupDir ($name + '.before.xml')) -Encoding Unicode
    }
}

$userId = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

foreach ($market in @('HK', 'US')) {
    $name = "NorthstarD1-Forward-$market-Manual"
    $action = New-ScheduledTaskAction `
        -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"E:\quant\research\northstar_d1_futu_sim\tools\run_integrated_manual.ps1`" -Market $market" `
        -WorkingDirectory 'E:\quant'
    $definition = New-ScheduledTask `
        -Action $action `
        -Principal $principal `
        -Settings $settings `
        -Description "Northstar-D1 $market on-demand manual validation; Futu SIMULATE only"
    Register-ScheduledTask -TaskName $name -InputObject $definition -Force | Out-Null
}

$installed = Get-ScheduledTask -TaskName $taskNames
foreach ($task in $installed) {
    if (-not $task.Settings.Enabled -or $task.State -eq 'Disabled') {
        throw "Manual task is not enabled: $($task.TaskName)"
    }
    [xml]$taskXml = Export-ScheduledTask -TaskName $task.TaskName
    $ns = New-Object System.Xml.XmlNamespaceManager($taskXml.NameTable)
    $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
    $triggerCount = @($taskXml.SelectNodes('/t:Task/t:Triggers/*', $ns)).Count
    if ($triggerCount -ne 0) {
        throw "Manual task unexpectedly has a trigger: $($task.TaskName)"
    }
    if ($task.Actions.Arguments -notmatch 'run_integrated_manual\.ps1') {
        throw "Manual task action mismatch: $($task.TaskName)"
    }
    Export-ScheduledTask -TaskName $task.TaskName |
        Set-Content -LiteralPath (Join-Path $backupDir ($task.TaskName + '.installed.xml')) -Encoding Unicode
}

$result = @{
    status = 'MANUAL_TASKS_INSTALLED'
    backup_dir = $backupDir
    tasks = @($installed | Sort-Object TaskName | ForEach-Object {
        @{
            task_name = $_.TaskName
            state = [string]$_.State
            enabled = [bool]$_.Settings.Enabled
            trigger_count = 0
            action = $_.Actions.Arguments
        }
    })
}
$receipt = Join-Path $backupDir 'manual_task_install_receipt.json'
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $receipt -Encoding UTF8
$result | ConvertTo-Json -Depth 6
