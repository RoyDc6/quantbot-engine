$ErrorActionPreference = 'Stop'

$stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$backupDir = Join-Path 'E:\quant\research\northstar_d1_futu_sim\migration_backups' $stamp
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$oldTasks = @(
    'NorthstarD1-HK',
    'NorthstarD1-US',
    'NorthstarD1-FutuSim-HK',
    'NorthstarD1-FutuSim-US'
)
foreach ($name in $oldTasks) {
    Export-ScheduledTask -TaskName $name |
        Set-Content -LiteralPath (Join-Path $backupDir ($name + '.xml')) -Encoding Unicode
}

$userId = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$actionHK = New-ScheduledTaskAction `
    -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
    -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "E:\quant\research\northstar_d1_futu_sim\tools\run_integrated_scheduled.ps1" -Market HK' `
    -WorkingDirectory 'E:\quant'
$actionUS = New-ScheduledTaskAction `
    -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
    -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "E:\quant\research\northstar_d1_futu_sim\tools\run_integrated_scheduled.ps1" -Market US' `
    -WorkingDirectory 'E:\quant'
$weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$triggerHK = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At '09:35'
$triggersUS = @(
    (New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At '21:35'),
    (New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At '22:35')
)

Register-ScheduledTask `
    -TaskName 'NorthstarD1-Forward-HK' `
    -Description 'Northstar-D1 HK integrated signal plus Futu SIMULATE forward execution' `
    -Action $actionHK -Trigger $triggerHK -Principal $principal -Settings $settings -Force | Out-Null
Register-ScheduledTask `
    -TaskName 'NorthstarD1-Forward-US' `
    -Description 'Northstar-D1 US integrated signal plus Futu SIMULATE forward execution with DST gate' `
    -Action $actionUS -Trigger $triggersUS -Principal $principal -Settings $settings -Force | Out-Null

$newTasks = Get-ScheduledTask -TaskName 'NorthstarD1-Forward-HK','NorthstarD1-Forward-US'
foreach ($task in $newTasks) {
    if (-not $task.Settings.Enabled -or $task.State -eq 'Disabled') {
        throw "New task is not enabled: $($task.TaskName)"
    }
    if ($task.Actions.Arguments -notmatch 'run_integrated_scheduled\.ps1') {
        throw "New task action mismatch: $($task.TaskName)"
    }
}
$hk = Get-ScheduledTask -TaskName 'NorthstarD1-Forward-HK'
$us = Get-ScheduledTask -TaskName 'NorthstarD1-Forward-US'
if (@($hk.Triggers).Count -ne 1 -or @($us.Triggers).Count -ne 2) {
    throw 'Integrated trigger count verification failed'
}

foreach ($name in $oldTasks) {
    Disable-ScheduledTask -TaskName $name | Out-Null
}
foreach ($task in $newTasks) {
    Export-ScheduledTask -TaskName $task.TaskName |
        Set-Content -LiteralPath (Join-Path $backupDir ($task.TaskName + '.installed.xml')) -Encoding Unicode
}

$result = @{
    status = 'INTEGRATED_TASKS_INSTALLED'
    backup_dir = $backupDir
    enabled = @($newTasks | Sort-Object TaskName | ForEach-Object { $_.TaskName })
    disabled = $oldTasks
    hk_triggers = @($hk.Triggers | ForEach-Object { $_.StartBoundary })
    us_triggers = @($us.Triggers | ForEach-Object { $_.StartBoundary })
}
$receipt = Join-Path $backupDir 'task_migration_receipt.json'
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receipt -Encoding UTF8
$result | ConvertTo-Json -Depth 5
