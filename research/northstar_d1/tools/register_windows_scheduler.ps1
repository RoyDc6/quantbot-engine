param(
    [switch]$EnableTasks
)

$ErrorActionPreference = "Stop"
$taskScript = "E:\quant\research\northstar_d1\tools\run_windows_scheduled_northstar.ps1"
$powerShell = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
$currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$principal = New-ScheduledTaskPrincipal -UserId $currentSid -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$contracts = @(
    @{
        Name = "NorthstarD1-HK"
        Market = "HK"
        Days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
        At = "16:20"
    },
    @{
        Name = "NorthstarD1-US"
        Market = "US"
        Days = @("Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
        At = "10:00"
    }
)

foreach ($contract in $contracts) {
    $action = New-ScheduledTaskAction `
        -Execute $powerShell `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$taskScript`" -Market $($contract.Market)" `
        -WorkingDirectory "E:\quant"
    $trigger = New-ScheduledTaskTrigger `
        -Weekly `
        -WeeksInterval 1 `
        -DaysOfWeek $contract.Days `
        -At $contract.At
    $task = New-ScheduledTask `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Northstar-D1 $($contract.Market) research-only completed-session report"
    Register-ScheduledTask -TaskName $contract.Name -InputObject $task -Force | Out-Null
    if ($EnableTasks) {
        Enable-ScheduledTask -TaskName $contract.Name | Out-Null
    }
    else {
        Disable-ScheduledTask -TaskName $contract.Name | Out-Null
    }
}
