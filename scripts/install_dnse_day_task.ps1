param([switch]$VerifyOnly)
$ErrorActionPreference = 'Stop'
$taskName = 'LiquidityLab - DNSE Daily Fallback'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonWindowlessPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$launcherArguments = '-m scripts.scheduled_dnse_entry'
if ((Get-TimeZone).Id -ne 'SE Asia Standard Time') {
    throw 'Scheduler expects SE Asia Standard Time; system timezone is not changed.'
}
if (-not (Test-Path -LiteralPath $pythonWindowlessPath)) { throw 'Project Python is missing.' }
if (-not $VerifyOnly) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Actions[0].Arguments -ne $launcherArguments -or $existing.Actions[0].WorkingDirectory -ne $projectRoot)) {
        throw 'Existing task belongs to another launcher; refusing overwrite.'
    }
    $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute $pythonWindowlessPath -Argument $launcherArguments -WorkingDirectory $projectRoot
    $weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '15:15'
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $userId
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($weekly,$logon) -Principal $principal -Settings $settings -Description 'DNSE read-only daily REST recovery, normalization, audits and approved web samples. Mon-Fri 15:15 Vietnam + user logon; resume missing closed weekdays within 7 calendar days from 2026-11-05. Interactive user; no stored password or trading orders.' -Force | Out-Null
}
$task = Get-ScheduledTask -TaskName $taskName
$info = Get-ScheduledTaskInfo -TaskName $taskName
$report = [ordered]@{
    task_name=$taskName; state=[string]$task.State; timezone=(Get-TimeZone).Id;
    verified_at_local=(Get-Date).ToString('o'); next_run_local=$info.NextRunTime.ToString('o');
    execute=$task.Actions[0].Execute; arguments=$task.Actions[0].Arguments; working_directory=$task.Actions[0].WorkingDirectory;
    logon_type=[string]$task.Principal.LogonType; last_task_result=$info.LastTaskResult;
    start_when_available=$task.Settings.StartWhenAvailable; wake_to_run=$task.Settings.WakeToRun;
    multiple_instances=[string]$task.Settings.MultipleInstances; restart_count=$task.Settings.RestartCount;
    trigger_summary='Mon-Fri 15:15 Vietnam + current-user logon; closed-session gates and seven-day local catchup';
    requires_computer_on_and_user_logged_in=$true; no_stored_password=$true;
    config='config/dnse_local_schedule.json'; stop_file='data/control/STOP_DNSE_HISTORY'
}
$target = Join-Path $projectRoot 'report\automation\dnse_task_registration.json'
New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $target -Encoding UTF8
$report | ConvertTo-Json -Depth 6
