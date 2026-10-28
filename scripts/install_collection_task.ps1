param([switch]$VerifyOnly)
$ErrorActionPreference = 'Stop'
$taskName = 'NEO LiquidityLab - Vnstock Whole Market'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonWindowlessPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$launcherArguments = '-m scripts.scheduled_entry'
$taskReport = Join-Path $projectRoot 'report\automation\task_registration.json'
if ((Get-TimeZone).Id -ne 'SE Asia Standard Time') {
    throw 'Scheduler expects Windows timezone SE Asia Standard Time; no system timezone is changed.'
}
if (-not $VerifyOnly) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        $oldAction = $existing.Actions | Select-Object -First 1
        if ($oldAction.Arguments -ne $launcherArguments -or $oldAction.WorkingDirectory -ne $projectRoot) {
            throw 'Existing task with this name belongs to a different launcher; refusing overwrite.'
        }
    }
    $action = New-ScheduledTaskAction -Execute $pythonWindowlessPath -Argument $launcherArguments -WorkingDirectory $projectRoot
    $weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '09:10'
    $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $userId
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8)
    $description = 'Collect all listed HOSE/HNX/UPCOM equities through authenticated Vnstock/KBS. Weekdays 09:10; bounded windows 09:15-11:30 and 13:00-14:30. Stop files/config gate NEO handoff. No trading orders. Interactive user, no stored password.'
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($weekly,$logon) -Principal $principal -Settings $settings -Description $description -Force | Out-Null
}
$task = Get-ScheduledTask -TaskName $taskName
$info = Get-ScheduledTaskInfo -TaskName $taskName
$report = [ordered]@{
    task_name=$taskName; state=[string]$task.State;
    verified_at_local=(Get-Date).ToString('o'); timezone=(Get-TimeZone).Id;
    next_run_local=$info.NextRunTime.ToString('o'); last_task_result=$info.LastTaskResult;
    execute=$task.Actions[0].Execute; arguments=$task.Actions[0].Arguments;
    working_directory=$task.Actions[0].WorkingDirectory;
    logon_type=[string]$task.Principal.LogonType; run_level=[string]$task.Principal.RunLevel;
    start_when_available=$task.Settings.StartWhenAvailable; wake_to_run=$task.Settings.WakeToRun;
    multiple_instances=[string]$task.Settings.MultipleInstances;
    requires_computer_on_and_user_logged_in=$true; no_stored_password=$true;
    trigger_summary='Mon-Fri 09:10 local + current-user logon; Python gates weekdays/windows/stop flags';
    enabled_config='config/market_collection.json'; stop_file='data/control/STOP_VNSTOCK'; neo_ready_file='data/control/NEO_READY'
}
New-Item -ItemType Directory -Path (Split-Path -Parent $taskReport) -Force | Out-Null
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $taskReport -Encoding UTF8
$report | ConvertTo-Json -Depth 6
